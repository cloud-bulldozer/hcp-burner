#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import configparser
import argparse
import subprocess
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient, SubscriptionClient
from azure.core.exceptions import HttpResponseError
from libs.platforms.platform import Platform
from libs.platforms.platform import PlatformArguments


class Aro(Platform):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["azure_region"] = arguments["azure_region"]
        self.environment["azure_credentials_file"] = arguments["azure_credentials_file"]
        self.environment['azure_mc_cluster_subscription'] = arguments['azure_mc_subscription']
        self.environment["aro_env"] = arguments["aro_env"]
        self.environment["aro_version"] = arguments["aro_version"]
        self.environment["aro_version_channel"] = arguments["aro_version_channel"]

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Aro")))
            parser.set_defaults(**defaults)

        temp_args, temp_unknown_args = parser.parse_known_args()
        if not temp_args.azure_credentials_file:
            parser.error("hcp-burner.py: error: the following arguments (or equivalent definition) are required: --azure-credentials-file")

    class EnvDefault(argparse.Action):
        def __init__(self, env, envvar, default=None, **kwargs):
            default = env[envvar] if envvar in env else default
            super(AroArguments.EnvDefault, self).__init__(
                default=default, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
