#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import configparser
import argparse
from libs.platforms.platform import Platform
from libs.platforms.platform import PlatformArguments


class Gcp(Platform):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["gcp_project_id"] = arguments["gcp_project_id"]
        self.environment["gcp_region"] = arguments["gcp_region"]
        self.environment["gcp_credentials_file"] = arguments["gcp_credentials_file"]
        self.environment["commands"].append("gcloud")

    def initialize(self):
        super().initialize()

        creds_file = self.environment["gcp_credentials_file"]
        self.logging.info(f"Verifying GCP credentials file {creds_file}...")
        with open(creds_file, 'r') as f:
            creds = json.load(f)
        for key in ["project_id", "client_email", "private_key"]:
            if key not in creds:
                self.logging.error(f"Missing {key} in GCP credentials file {creds_file}")
                sys.exit("Exiting...")
        self.logging.info(f"GCP credentials file {creds_file} verified")

        self.logging.info("Authenticating with GCP using service account")
        auth_code, _, _ = self.utils.subprocess_exec(
            f"gcloud auth activate-service-account {creds['client_email']} --key-file={creds_file}"
        )
        if auth_code != 0:
            self.logging.error("Failed to authenticate with GCP")
            sys.exit("Exiting...")

        set_code, _, _ = self.utils.subprocess_exec(
            f"gcloud config set project {self.environment['gcp_project_id']}"
        )
        if set_code != 0:
            self.logging.error(f"Failed to set GCP project {self.environment['gcp_project_id']}")
            sys.exit("Exiting...")
        self.logging.info(f"GCP project set to {self.environment['gcp_project_id']}")

    def platform_cleanup(self):
        super().platform_cleanup()

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)

    def get_workers_ready(self, kubeconfig, cluster_name):
        super().get_workers_ready(kubeconfig, cluster_name)
        return Platform.get_workers_ready(self, kubeconfig, cluster_name)

    def get_metadata(self, platform, cluster_name):
        super().get_metadata(platform, cluster_name)
        metadata = {}
        return metadata

    def watcher(self):
        super().watcher()


class GcpArguments(PlatformArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--gcp-project-id", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_PROJECT_ID", help="GCP Project ID")
        parser.add_argument("--gcp-region", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_REGION", default="us-central1", help="GCP Region")
        parser.add_argument("--gcp-credentials-file", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_CREDENTIALS_FILE", help="GCP service account credentials JSON file")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Gcp")))
            parser.set_defaults(**defaults)

        temp_args, temp_unknown_args = parser.parse_known_args()
        if not temp_args.gcp_project_id or not temp_args.gcp_credentials_file:
            parser.error("hcp-burner.py: error: the following arguments (or equivalent definition) are required: --gcp-project-id, --gcp-credentials-file")

    class EnvDefault(argparse.Action):
        def __init__(self, env, envvar, default=None, **kwargs):
            default = env[envvar] if envvar in env else default
            super(GcpArguments.EnvDefault, self).__init__(
                default=default, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
