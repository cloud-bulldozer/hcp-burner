#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module to set AWS related variables
"""

import configparser
import os
import sys


class AWS:
    """AWS Class"""

    def __init__(self, logging, account_file, profile):
        self.logging = logging
        self.config_file = account_file
        if os.path.exists(account_file):
            self.logging.info("AWS account file found. Loading account information")
            self.aws_config = configparser.RawConfigParser()
            self.aws_config.read(account_file)
            if len(self.aws_config.sections()) == 1:
                profile = self.aws_config.sections()[0]
            else:
                if not profile:
                    self.logging.error("Multiple profiles detected on AWS credentials file but no --aws-profile parameter")
                    sys.exit("Exiting...")
                else:
                    if profile not in self.aws_config.sections():
                        self.logging.error(f"Profile {profile} especified as --aws-profile not found on AWS credentials file {account_file}")
                        sys.exit("Exiting...")
            if ("aws_access_key_id" not in self.aws_config[profile] or "aws_secret_access_key" not in self.aws_config[profile]):
                self.logging.error(f"Missing credentials on file {account_file} for profile {profile}")
                sys.exit("Exiting...")
            else:
                self.logging.info(f"AWS configuration verified for profile {profile} on file {account_file}")
                self.logging.debug(f"AWS Profile: {self.aws_config[profile]}")
        else:
            self.config_file = account_file
            self.logging.info("AWS Account file is not provided, so aws environment variables are being used")

    def set_aws_envvars(self, profile, aws_region):
        """ Get AWS information from the account_file if provided and set related environment vars"""
        if self.config_file != "":
            profile = self.aws_config.sections()[0] if len(self.aws_config.sections()) == 1 else profile
            os.environ["AWS_PROFILE"] = profile
            os.environ["AWS_REGION"] = aws_region
            os.environ["AWS_ACCESS_KEY_ID"] = self.aws_config[profile]["aws_access_key_id"]
            os.environ["AWS_SECRET_ACCESS_KEY"] = self.aws_config[profile]["aws_secret_access_key"]
            os.environ["AWS_SHARED_CREDENTIALS_FILE"] = self.config_file

    def set_aws_environment(self, profile, aws_region):
        """ Get AWS information from the account_file if provided and save it on the environment object"""
        aws = {}
        if self.config_file == "":
            self.logging.info("AWS Account file is not provided, so aws environment variables are being used")
            aws['aws_access_key_id'] = os.environ["AWS_ACCESS_KEY_ID"]
            aws['aws_secret_access_key'] = os.environ["AWS_SECRET_ACCESS_KEY"]
        else:
            profile = self.aws_config.sections()[0] if len(self.aws_config.sections()) == 1 else profile
            aws['aws_access_key_id'] = self.aws_config[profile]["aws_access_key_id"]
            aws['aws_secret_access_key'] = self.aws_config[profile]["aws_secret_access_key"]
        aws['profile'] = profile
        aws['region'] = aws_region
        return aws
