#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import configparser
import argparse
from libs.platforms.platform import Platform
from libs.platforms.platform import PlatformArguments


class Azure(Platform):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["commands"].append("az")
        self.environment["azure_region"] = arguments["azure_region"]
        self.environment["azure_credentials_file"] = arguments["azure_credentials_file"]
        self.environment['azure_mc_cluster_subscription'] = arguments['azure_mc_subscription']

    def initialize(self):
        super().initialize()
        # Verify Azure credentials file
        self.logging.info(f"Verifying Azure Credentials File {self.environment['azure_credentials_file']}...")
        with open(self.environment["azure_credentials_file"], 'r') as azure_credentials_file:
            content = azure_credentials_file.read()
            for key in ["tenantId", "subscriptionId", "ClientId", "ClientSecret"]:
                if key not in content:
                    self.logging.error(f"Missing {key} on Azure credentials file {self.environment['azure_credentials_file']}")
                    sys.exit("Exiting...")
            self.logging.info(f"Azure Credentials File {self.environment['azure_credentials_file']} verified")
        with open(self.environment["azure_credentials_file"], 'r') as azure_credentials_file:
            for line in azure_credentials_file:
                if line.startswith('subscriptionId:'):
                    self.environment['subscription_id'] = line.split(':', 1)[1].strip().strip('"')
        # Verify Azure Login and subscription
        self.logging.info("Getting azure subscriptions using  `az account list`")
        az_account_code, az_account_out, az_account_err = self.utils.subprocess_exec("az account list")
        sys.exit("Exiting...") if az_account_code != 0 else self.logging.info("`az account list` execution OK")

        for subscription in json.loads(az_account_out):
            if subscription["id"] == self.environment["subscription_id"]:
                az_account_set_code, az_account_set_out, az_account_set_err = self.utils.subprocess_exec("az account set --subscription " + subscription["id"])
                if az_account_set_code != 0:
                    self.logging.error(f"Failed to set {subscription['id']} for user {subscription['user']['name']}")
                    self.logging.error(az_account_set_out)
                    self.logging.error(az_account_set_err)
                    self.logging.debug(subscription)
                else:
                    self.logging.info(F"Set subscription ID {subscription['id']} to user {subscription['user']['name']} OK")
                    return 0
        self.logging.error(f"Subscription ID {self.environment['subscription_id']} not found")
        self.logging.debug(json.loads(az_account_out))
        sys.exit("Exiting...")

    def get_az_aks_cluster_info(self, cluster_name, resourcegroup):
        if not self.environment['azure_mc_cluster_subscription']:
            self.logging.warning(f"MC Cluster subscription is not provided, using subscription from {self.environment['azure_credentials_file']}")
            subscription = self.environment['subscription_id']
        else:
            subscription = self.environment['azure_mc_cluster_subscription']
        self.logging.info(f"Get Cluster metadata of {cluster_name} on {resourcegroup}")
        resp_code, resp_out, resp_err = self.utils.subprocess_exec("az aks show --subscription " + subscription + " --resource-group " + resourcegroup + " --name " + cluster_name + " --output json", extra_params={"universal_newlines": True})
        try:
            cluster = json.loads(resp_out)
        except Exception as err:
            self.logging.error(f"Cannot load metadata for cluster {cluster_name}")
            self.logging.error(err)
        metadata = {}
        metadata['cluster_name'] = cluster.get("name", None)
        metadata['infra_id'] = cluster.get("fqdn", None)
        metadata['cluster_id'] = cluster.get("id", None)
        metadata['version'] = cluster.get("currentKubernetesVersion", None)
        metadata['base_domain'] = cluster.get("dnsPrefix", None)
        metadata['location'] = cluster.get("location", None)
        metadata['network_type'] = cluster.get("networkProfile", {}).get("networkDataplane", None)
        for profile in cluster.get("agentPoolProfiles", []):
            if profile.get("name", None) == "userpool":
                metadata['workers'] = profile.get("count", None)
                metadata['workers_min'] = profile.get("minCount", None)
                metadata['workers_max'] = profile.get("maxCount", None)
                metadata['workers_type'] = profile.get("vmSize", None)
        return metadata

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
        # TODO Implement metadata logic when ready to use
        return metadata

    def watcher(self):
        super().watcher()


class AzureArguments(PlatformArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--azure-credentials-file", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_CREDENTIALS_FILE", help="Azure credentials file")
        parser.add_argument("--azure-region", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_REGION", default='eastus', help="Azure Region")
        parser.add_argument("--azure-mc-subscription", action=EnvDefault, env=environment, envvar="HCP_BURNER_MC_SUBSCRIPTION", help="Azure Subscription where MC Cluster is installed")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Azure")))
            parser.set_defaults(**defaults)

        temp_args, temp_unknown_args = parser.parse_known_args()
        if not temp_args.azure_credentials_file:
            parser.error("hcp-burner.py: error: the following arguments (or equivalent definition) are required: --azure-credentials-file")

    class EnvDefault(argparse.Action):
        def __init__(self, env, envvar, default=None, **kwargs):
            default = env[envvar] if envvar in env else default
            super(AzureArguments.EnvDefault, self).__init__(
                default=default, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
