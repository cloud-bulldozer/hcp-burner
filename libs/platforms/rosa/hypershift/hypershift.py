#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import re
import os
import time
import datetime
import math
import shutil
import concurrent.futures
import configparser
from copy import deepcopy

from libs.platforms.rosa.rosa import Rosa
from libs.platforms.rosa.rosa import RosaArguments


class Hypershift(Rosa):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        pattern = re.compile(r"^(\d+)(,\s*\d+)*$")
        if arguments["workers"].isdigit() and int(arguments["workers"]) % 3 != 0:
            self.logging.error(f"Invalid value ({arguments['workers']}) for parameter  `--workers`. If digit, it must be divisible by 3'")
        elif bool(pattern.match(arguments["workers"])):
            for num in arguments["workers"].split(","):
                if int(num) < 3 or int(num) % 3 != 0:
                    self.logging.error(f"Invalid value ({num}) for parameter `--workers`. If list, all values must be divisible by 3")
                    sys.exit("Exiting...")

        self.environment["workers"] = arguments["workers"]

        self.environment["service_cluster"] = arguments["service_cluster"]

        self.environment["create_vpcs"] = arguments["create_vpcs"]
        self.environment["delete_vpcs"] = arguments["delete_vpcs"]
        if str(arguments["create_vpcs"]).lower() == "true" or str(arguments["delete_vpcs"]).lower() == "true":
            self.environment["commands"].append("terraform")
            self.environment["clusters_per_vpc"] = arguments["clusters_per_vpc"]
            self.environment["terraform_retry"] = arguments["terraform_retry"]
        else:
            if (arguments["install_clusters"]) and (arguments["wildcard_options"] and "--subnets-ids" not in arguments["wildcard_options"] or not arguments["wildcard_options"]):
                self.logging.error("Cluster creation will fail. No subnets are provided and no --create-vpcs command is selected")
                sys.exit("Exiting...")
            else:
                self.logging.info(f"No VPC will be created, using {arguments['wildcard_options']}")

    def initialize(self):
        super().initialize()

        # Set Provision Shard
        if self.environment["service_cluster"]:
            self.logging.info(f"Verifying Provision Shard for Service Cluster: {self.environment['service_cluster']}")
            self.environment["shard_id"] = self._verify_provision_shard()
            sys.exit("Exiting...") if self.environment["shard_id"] is None else self.logging.info(f"Found provision shard {self.environment['shard_id']} for Service Cluster {self.environment['service_cluster']}")
            self.environment["sc_kubeconfig"] = self.download_kubeconfig(self.environment["service_cluster"], self.environment["path"])

        # Set OIDC Config
        self.logging.info("Verifying OIDC config")
        sys.exit("Exiting") if not self._set_oidc_config() else self.logging.info(f"Using {self.environment['oidc_config_id']} as OIDC config ID")

        # Create Account roles
        self.logging.info("Creating ROSA Account roles")
        sys.exit("Exiting") if not self._create_rosa_account_roles() else self.logging.info("Created Account roles successfully")

        # Set Operator Roles
        self.logging.info("Verifying Operator Roles")
        if self.environment["common_operator_roles"]:
            sys.exit("Exiting") if not self._create_operator_roles() else self.logging.info(f"Using {self.environment['cluster_name_seed']} as Operator Roles Prefix")

        # Create VPCs
        if self.environment["create_vpcs"]:
            vpcs_to_create = math.ceil(self.environment["cluster_count"] / self.environment["clusters_per_vpc"])
            self.logging.info(f"Clusters Requested: {self.environment['cluster_count']}. Clusters Per VPC: {self.environment['clusters_per_vpc']}. VPCs to create: {vpcs_to_create}")
            os.mkdir(self.environment["path"] + "/terraform")
            shutil.copyfile(
                sys.path[0] + "/libs/platforms/rosa/hypershift/terraform/setup-vpcs.tf",
                self.environment["path"] + "/terraform/setup-vpcs.tf",
            )
            self.environment["vpcs"] = self._create_vpcs(vpcs_to_create)
            if len(self.environment["vpcs"]) == 0:
                self.logging.error("Failed to create AWS VPCs, jumping to cleanup and exiting...")
                self.platform_cleanup()
                sys.exit("Exiting")
            else:
                self.logging.info(f"Created {len(self.environment['vpcs'])} AWS VPCs")

    def _verify_provision_shard(self):
        self.logging.debug(self.environment['aws'])
        shard_code, shard_out, shard_err = self.utils.subprocess_exec(
            f"ocm get /api/clusters_mgmt/v1/provision_shards?search=region.id+is+%27{self.environment['aws']['region']}%27"
        )
        if shard_code == 0:
            for shard in json.loads(shard_out.decode("utf-8")).get("items", {}):
                if self.environment["service_cluster"] in shard.get(
                    "hypershift_config", {}
                ).get("server", {}):
                    # hypershift_config.server is the service cluster, like https: // api.hs-sc-0vfs0cl5g.wqrn.s1.devshift.org: 6443. split('.')[1] will return hs-sc-0vfs0cl5g
                    return shard["id"]
        self.logging.error(f"No Provision Shard found for Service Cluster {self.environment['service_cluster']} on {self.environment['aws']['region']}")
        return None

    def _get_mc(self, cluster_id):
        self.logging.debug(f"Get the mgmt cluster of cluster {cluster_id}")
        resp_code, resp_out, resp_err = self.utils.subprocess_exec(
            "ocm get /api/clusters_mgmt/v1/clusters/" + cluster_id + "/hypershift",
            extra_params={"universal_newlines": True},
        )
        return json.loads(resp_out).get("management_cluster", None) if resp_code == 0 else None

    # Get Hypershift cluster metadata and set required platform environment variables
    def get_metadata(self, platform, cluster_name):
        metadata = super().get_metadata(platform, cluster_name)
        self.logging.info(f"Getting information for cluster {cluster_name}")
        metadata_code, metadata_out, metadata_err = self.utils.subprocess_exec(
            "rosa describe cluster -c " + cluster_name + " -o json",
            extra_params={"universal_newlines": True}
        )
        try:
            status = json.loads(metadata_out)["state"]
        except Exception as err:
            self.logging.error(f"Cannot load metadata for cluster {cluster_name}")
            self.logging.error(err)

        if status == "ready":
            cluster_mc = self._get_mc(self.get_cluster_id(cluster_name))
            metadata["mgmt_cluster_name"] = cluster_mc
            platform.environment["mc_kubeconfig"] = platform.environment["path"] + "/kubeconfig_" + cluster_mc

        return metadata

    def platform_cleanup(self):
        super().platform_cleanup()
        self.logging.info("Cleaning resources")
        # Delete Operator Roles
        self._delete_operator_roles() if self.environment["common_operator_roles"] else None
        # Delete oidc-config
        self._delete_oidc_config() if self.environment["oidc_cleanup"] else None
        # Delete VPCs
        self._destroy_vpcs() if (self.environment["create_vpcs"] or self.environment["delete_vpcs"]) else None

    def _create_vpcs(self, vpcs_to_create):
        self.logging.info("Initializing Terraform with: terraform init")
        terraform_code, terraform_out, terraform_err = self.utils.subprocess_exec(
            "terraform init",
            self.environment["path"] + "/terraform/terraform-version.log",
            {"cwd": self.environment["path"] + "/terraform"},
        )
        if terraform_code == 0:
            self.logging.info(
                f"Applying terraform plan command with: terraform apply for {vpcs_to_create} VPC(s), using {self.environment['cluster_name_seed']} as name seed on {self.environment['aws']['region']}"
            )
            for trying in range(1, self.environment["terraform_retry"] + 1):
                self.logging.info("Try: %d. Starting terraform apply" % trying)
                myenv = os.environ.copy()
                myenv["TF_VAR_cluster_name_seed"] = self.environment[
                    "cluster_name_seed"
                ]
                myenv["TF_VAR_cluster_count"] = str(vpcs_to_create)
                myenv["TF_VAR_aws_region"] = self.environment["aws"]["region"]
                apply_code, apply_out, apply_err = self.utils.subprocess_exec(
                    "terraform apply --auto-approve",
                    self.environment["path"] + "/terraform/terraform-apply.log",
                    {"cwd": self.environment["path"] + "/terraform", "env": myenv},
                )
                if apply_code == 0:
                    self.logging.info(
                        "Applied terraform plan command with: terraform apply"
                    )
                    try:
                        with open(
                            self.environment["path"] + "/terraform/terraform.tfstate",
                            "r",
                        ) as terraform_file:
                            json_output = json.load(terraform_file)
                    except Exception as err:
                        self.logging.error(err)
                        self.logging.error(
                            "Try: %d. Failed to read terraform output file %s"
                            % (
                                trying,
                                self.environment["path"]
                                + "/terraform/terraform.tfstate",
                            )
                        )
                        return []
                    vpcs = []
                    # Check if we have IDs for everything
                    number_of_vpcs = len(json_output["outputs"]["vpc-id"]["value"])
                    number_of_public = len(
                        json_output["outputs"]["cluster-public-subnets"]["value"]
                    )
                    number_of_private = len(
                        json_output["outputs"]["cluster-private-subnets"]["value"]
                    )
                    if (
                        number_of_vpcs != vpcs_to_create
                        or number_of_public != vpcs_to_create
                        or number_of_private != vpcs_to_create
                    ):
                        self.logging.info(
                            "Required Clusters: %d" % self.environment["cluster_count"]
                        )
                        self.logging.info("Number of VPCs: %d" % number_of_vpcs)
                        self.logging.info(
                            "Number of Private Subnets: %d" % number_of_private
                        )
                        self.logging.info(
                            "Number of Public Subnets: %d" % number_of_public
                        )
                        self.logging.info(
                            "Number of Clusters per VPC: %d" % self.environment["clusters_per_vpc"]
                        )
                        self.logging.warning(
                            "Try %d: Not all resources has been created. retring in 15 seconds"
                            % trying
                        )
                        time.sleep(15)
                    else:
                        # preparing vpcs dict with network and subnet details
                        for cluster in range(vpcs_to_create):
                            # internal loop iterates over number of cluster per vpc
                            # if cluster_per_pvc is set to 2, first 2 clusters share the same VPC
                            for itr in range(self.environment["clusters_per_vpc"]):
                                vpc_id = json_output["outputs"]["vpc-id"]["value"][cluster]
                                public_subnets = json_output["outputs"][
                                    "cluster-public-subnets"
                                ]["value"][cluster]
                                private_subnets = json_output["outputs"][
                                    "cluster-private-subnets"
                                ]["value"][cluster]
                                if len(public_subnets) != 3 or len(private_subnets) != 3:
                                    self.logging.warning(
                                        "Try: %d. Number of public subnets of VPC %s: %d (required: 3)"
                                        % (trying, vpc_id, len(public_subnets))
                                    )
                                    self.logging.warning(
                                        "Try: %d. Number of private subnets of VPC %s: %d (required: 3)"
                                        % (trying, vpc_id, len(private_subnets))
                                    )
                                    self.logging.warning(
                                        "Try: %d: Not all subnets created, retring in 15 seconds"
                                        % trying
                                    )
                                    time.sleep(15)
                                else:
                                    self.logging.debug(
                                        "VPC ID: %s, Public Subnet: %s, Private Subnet: %s"
                                        % (vpc_id, public_subnets, private_subnets)
                                    )
                                    subnets = ",".join(public_subnets)
                                    subnets = subnets + "," + ",".join(private_subnets)
                                    vpcs.append((vpc_id, subnets))
                        return vpcs
                else:
                    self.logging.warning(
                        "Try: %d. Unable to execute terraform apply, retrying in 15 seconds"
                    )
                    time.sleep(15)
            self.logging.error(
                "Failed to appy terraform plan after %d retries"
                % self.environment["terraform_retry"]
            )
        self.logging.error(
            "Failed to initialize terraform on %s" % self.environment["path"]
            + "/terraform"
        )
        return []

    def _destroy_vpcs(self):
        for trying in range(1, self.environment["terraform_retry"] + 1):
            # if args.manually_cleanup_secgroups:
            #     for cluster in vpcs:
            #         logging.info("Try: %d. Starting manually destroy of security groups" % trying)
            #         _delete_security_groups(aws_region, path, cluster[0])
            self.logging.info("Try: %d. Starting terraform destroy process" % trying)
            destroy_code, destroy_out, destroy_err = self.utils.subprocess_exec(
                "terraform destroy --auto-approve",
                self.environment["path"] + "/terraform/terraform-destroy.log",
                {"cwd": self.environment["path"] + "/terraform"},
            )
            if destroy_code == 0:
                self.logging.info("Try: %d. All VPCs destroyed" % trying)
                return 0
            else:
                self.logging.error(
                    "Try: %d. Failed to execute terraform destroy, retrying in 15 seconds"
                    % trying
                )
                time.sleep(15)
        self.logging.error(
            "Failed to destroy VPCs after %d retries"
            % self.environment["terraform_retry"]
        )
        return 1

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
        cluster_info["install_method"] = "rosa"
        cluster_info["mgmt_cluster_name"] = self._get_mc(cluster_info["metadata"]["cluster_id"])
        self.logging.info(f"Deleting cluster {cluster_name} on Hypershift Platform")
        cleanup_code, cleanup_out, cleanup_err = self.utils.subprocess_exec("rosa delete cluster -c " + cluster_name + " -y --watch", cluster_info["path"] + "/cleanup.log", {'preexec_fn': self.utils.disable_signals})
        cluster_delete_end_time = int(datetime.datetime.utcnow().timestamp())
        if cleanup_code == 0:
            self.logging.debug(
                f"Confirm cluster {cluster_name} deleted by attempting to describe the cluster. This should fail if the cluster is removed."
            )
            check_code, check_out, check_err = self.utils.subprocess_exec(
                "rosa describe cluster -c " + cluster_name, log_output=False
            )
            if check_code != 0:
                cluster_info["status"] = "deleted"
                operator_role_prefix = cluster_info["metadata"]["operator_role_prefix"]
                self.logging.debug(
                    f"Destroying STS associated resources of cluster name: {cluster_name}"
                )
                (operators_code, operators_out, operators_err) = self.utils.subprocess_exec("rosa delete operator-roles --prefix " + operator_role_prefix + " -m auto -y", cluster_info["path"] + "/operator-role-cleanup.log", {'preexec_fn': self.utils.disable_signals})
                if operators_code != 0:
                    self.logging.error(
                        f"Failed to delete operator roles on cluster {cluster_name}"
                    )
                    cluster_info["status"] = "deleted but roles"
            else:
                self.logging.error(
                    f"Cluster {cluster_name} still in list of clusters. Not Removing Roles"
                )
                cluster_info["status"] = "not deleted"
        else:
            cluster_info["status"] = "not deleted"
        cluster_end_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["destroy_duration"] = cluster_delete_end_time - cluster_start_time
        cluster_info["destroy_all_duration"] = cluster_end_time - cluster_start_time
        try:
            with open(cluster_info['path'] + "/metadata_destroy.json", "w") as metadata_file:
                json.dump(cluster_info, metadata_file)
        except Exception as err:
            self.logging.error(err)
            self.logging.error(f"Failed to write metadata_install.json file located at {cluster_info['path']}")
        if self.es is not None:
            self.es.index_metadata(cluster_info)

    def _get_aws_role_name(self, cluster_name):
        # Required by OCM-3187 (https://issues.redhat.com/browse/OCM-3187), remove when fixed
        (role_policy_code, role_policy_out, role_policy_err) = self.utils.subprocess_exec("rosa describe cluster -c " + cluster_name + " -o json")
        if role_policy_code == 0:
            for role in json.loads(role_policy_out.decode("utf-8")).get("aws", {}).get("sts", {}).get("operator_iam_roles", []):
                if role.get("name", "") == "kube-controller-manager":
                    return role.get("role_arn").split("/")[-1]
        self.logging.error(f"No Role named kube-controller-manager found on Cluster {cluster_name}")
        return None

    def _wait_for_workers(
        self, kubeconfig, worker_nodes, wait_time, cluster_name, machinepool_name
    ):
        self.logging.info(
            f"Waiting {wait_time} minutes for {worker_nodes} workers to be ready on {machinepool_name} machinepool on {cluster_name}"
        )
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        result = [machinepool_name]
        starting_time = datetime.datetime.utcnow().timestamp()
        self.logging.debug(
            f"Waiting {wait_time} minutes for nodes to be Ready on cluster {cluster_name} until {datetime.datetime.fromtimestamp(starting_time + wait_time * 60)}"
        )
        while datetime.datetime.utcnow().timestamp() < starting_time + wait_time * 60:
            # if force_terminate:
            #     logging.error("Exiting workers waiting on the cluster %s after capturing Ctrl-C" % cluster_name)
            #     return []
            self.logging.info("Getting node information for cluster %s" % cluster_name)
            nodes_code, nodes_out, nodes_err = self.utils.subprocess_exec(
                "oc get nodes -o json",
                extra_params={"env": myenv, "universal_newlines": True},
            )
            try:
                nodes_json = json.loads(nodes_out)
            except Exception as err:
                self.logging.error(
                    f"Cannot load command result for cluster {cluster_name}. Waiting 15 seconds for next check..."
                )
                self.logging.error(err)
                time.sleep(15)
                continue
            nodes = nodes_json["items"] if "items" in nodes_json else []

            # First we find nodes which label nodePool match the machinepool name and then we check if type:Ready is on the conditions
            ready_nodes = (
                sum(
                    len(
                        list(
                            filter(
                                lambda x: x.get("type") == "Ready"
                                and x.get("status") == "True",
                                node["status"]["conditions"],
                            )
                        )
                    )
                    for node in nodes
                    if node.get("metadata", {})
                    .get("labels", {})
                    .get("hypershift.openshift.io/nodePool")
                    and machinepool_name
                    in node["metadata"]["labels"]["hypershift.openshift.io/nodePool"]
                )
                if nodes
                else 0
            )

            if ready_nodes == worker_nodes:
                self.logging.info(
                    f"Found {ready_nodes}/{worker_nodes} ready nodes on machinepool {machinepool_name} for cluster {cluster_name}. Stopping wait."
                )
                result.append(ready_nodes)
                result.append(int(datetime.datetime.utcnow().timestamp()))
                return result
            else:
                self.logging.info(
                    f"Found {ready_nodes}/{worker_nodes} ready nodes on machinepool {machinepool_name} for cluster {cluster_name}. Waiting 15 seconds for next check..."
                )
                time.sleep(15)
        self.logging.error(
            f"Waiting time expired. After {wait_time} minutes there are {ready_nodes}/{worker_nodes} ready nodes on {machinepool_name} machinepool for cluster {cluster_name}"
        )
        result.append(ready_nodes)
        result.append("")
        return result

    def _get_aws_account_id(self):
        # Required by OCM-3187 (https://issues.redhat.com/browse/OCM-3187), remove when fixed
        (acc_id_code, acc_id_out, acc_id_err) = self.utils.subprocess_exec("aws sts get-caller-identity --output json")
        if acc_id_code == 0:
            acc_id = json.loads(acc_id_out)
            return acc_id['Account']
        self.logging.error("Cannot find AWS Account information for the given credentials")
        return None

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
        cluster_info["hostedclusters"] = self.environment["cluster_count"]
        cluster_info["environment"] = self.environment["rosa_env"]
        cluster_info["install_method"] = "rosa"
        self.logging.info(f"Creating cluster {cluster_info['index']} on Hypershift with name {cluster_name} and {cluster_info['workers']} workers")
        cluster_info["path"] = platform.environment["path"] + "/" + cluster_name
        os.mkdir(cluster_info["path"])
        self.logging.debug("Attempting cluster installation")
        self.logging.debug("Output directory set to %s" % cluster_info["path"])
        cluster_cmd = ["rosa", "create", "cluster", "--cluster-name", cluster_name, "--replicas", str(cluster_info["workers"]), "--hosted-cp", "--sts", "--mode", "auto", "-y", "--output", "json", "--oidc-config-id", platform.environment["oidc_config_id"], "--region", platform.environment["aws"]["region"]]
        if platform.environment["create_vpcs"]:
            self.logging.debug(platform.environment["vpcs"][(cluster_info["index"])])
            cluster_info["vpc"] = platform.environment["vpcs"][(cluster_info["index"])]
            cluster_cmd.append("--subnet-ids")
            cluster_cmd.append(cluster_info["vpc"][1])
        if "shard_id" in platform.environment:
            cluster_cmd.append("--properties")
            cluster_cmd.append("provision_shard_id:" + platform.environment["shard_id"])
        if platform.environment["wildcard_options"]:
            for param in platform.environment["wildcard_options"].split():
                cluster_cmd.append(param)
        if self.environment["common_operator_roles"]:
            cluster_cmd.append("--operator-roles-prefix")
            cluster_cmd.append(self.environment["cluster_name_seed"])
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())
        self.logging.info(f"Trying to install cluster {cluster_name} with {cluster_info['workers']} workers up to 5 times")
        trying = 0
        while trying <= 5:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting cluster creation for {cluster_name} after capturing Ctrl-C")
                return 0
            self.logging.info("Cluster Create Command:")
            self.logging.info(cluster_cmd)
            (create_cluster_code, create_cluster_out, create_cluster_err) = self.utils.subprocess_exec(" ".join(str(x) for x in cluster_cmd), cluster_info["path"] + "/rosa-create.log", {'preexec_fn': self.utils.disable_signals})
            trying += 1
            if create_cluster_code != 0:
                cluster_info["install_try"] = trying
                self.logging.debug(create_cluster_out)
                self.logging.debug(create_cluster_err)
                if trying <= 5:
                    self.logging.warning(f"Try: {trying}/5. Cluster {cluster_name} installation failed, retrying in 15 seconds")
                    time.sleep(15)
                else:
                    cluster_end_time = int(datetime.datetime.utcnow().timestamp())
                    cluster_info["status"] = "Not Installed"
                    self.logging.error(f"Cluster {cluster_name} installation failed after 5 retries")
                    self.logging.debug(create_cluster_out)
                    return 1
            else:
                break

        # Required by OCM-3187 (https://issues.redhat.com/browse/OCM-3187), remove when fixed
        self.logging.info(f"Getting kube-controller-manager role for cluster {cluster_name}")
        aws_role_name = self._get_aws_role_name(cluster_name)
        aws_account_id = self._get_aws_account_id()
        self.logging.info(f"Found kube-controller-manager role {aws_role_name} for cluster {cluster_name}")
        (aws_policy_code, aws_policy_out, aws_policy_err) = self.utils.subprocess_exec("aws iam attach-role-policy --role-name " + aws_role_name + " --policy-arn arn:aws:iam::" + aws_account_id + ":policy/hack-414-custom-policy")
        if aws_policy_code != 0:
            cluster_info['status'] = "aws policy failed"
            return 1
        else:
            self.logging.info(f"Patched kube-controller-manager role {aws_role_name} for cluster {cluster_name} with policy arn:aws:iam::415909267177:policy/hack-414-custom-policy")

        cluster_info['status'] = "Installing"
        self.logging.info(f"Cluster {cluster_name} installation started on the {trying} try")
        cluster_info["metadata"] = self.get_metadata(platform, cluster_name)
        cluster_info["install_try"] = trying
        with concurrent.futures.ThreadPoolExecutor() as executor:
            preflight_ch = executor.submit(self._preflight_wait, cluster_info["metadata"]["cluster_id"], cluster_name)
            sc_namespace = executor.submit(self._namespace_wait, platform.environment["sc_kubeconfig"], cluster_info["metadata"]["cluster_id"], cluster_name, "Service") if platform.environment["sc_kubeconfig"] != "" else 0
            cluster_info["preflight_checks"] = preflight_ch.result()
            cluster_info["sc_namespace_timing"] = sc_namespace.result() - cluster_start_time if platform.environment["sc_kubeconfig"] != "" else None

            mgmt_cluster_name = self._get_mc(cluster_info["metadata"]["cluster_id"])
            self.environment["mc_kubeconfig"] = self.download_kubeconfig(mgmt_cluster_name, self.environment["path"])
            mc_namespace = executor.submit(self._namespace_wait, platform.environment["mc_kubeconfig"], cluster_info["metadata"]["cluster_id"], cluster_name, "Management") if platform.environment["mc_kubeconfig"] != "" else 0
            cluster_info["mc_namespace_timing"] = mc_namespace.result() - cluster_start_time if platform.environment["mc_kubeconfig"] != "" else None
        cluster_start_time_on_mc = mc_namespace.result()
        watch_code, watch_out, watch_err = self.utils.subprocess_exec("rosa logs install -c " + cluster_name + " --watch", cluster_info["path"] + "/installation.log", {'preexec_fn': self.utils.disable_signals})
        if watch_code != 0:
            cluster_info['status'] = "not ready"
            return 1
        else:
            cluster_info['status'] = "installed"
            cluster_end_time = int(datetime.datetime.utcnow().timestamp())
            # Getting againg metadata to update the cluster status
            cluster_info["metadata"] = self.get_metadata(platform, cluster_name)
            cluster_info['cluster_start_time_on_mc'] = cluster_start_time_on_mc  # excludes pre-flight durations
            cluster_info['cluster_end_time'] = cluster_end_time
            cluster_info["install_duration"] = cluster_end_time - cluster_start_time
            access_timers = self.get_cluster_admin_access(cluster_name, cluster_info["path"])
            cluster_info["kubeconfig"] = access_timers.get("kubeconfig", None)
            cluster_info["cluster_admin_create"] = access_timers.get("cluster_admin_create", None)
            cluster_info["cluster_admin_login"] = access_timers.get("cluster_admin_login", None)
            cluster_info["cluster_oc_adm"] = access_timers.get("cluster_oc_adm", None)
            if not cluster_info["kubeconfig"]:
                self.logging.error(f"Failed to download kubeconfig file for cluster {cluster_name}. Disabling wait for workers and workload execution")
                cluster_info["workers_wait_time"] = None
                cluster_info["status"] = "Ready. Not Access"
                return 1
            if "extra_machinepool" in platform.environment:
                extra_machine_pool_start_time = int(datetime.datetime.utcnow().timestamp())
                self.add_machinepool(cluster_name, cluster_info["metadata"]["cluster_id"], cluster_info["metadata"]["zones"], platform.environment["extra_machinepool"])
            if cluster_info["workers_wait_time"]:
                with concurrent.futures.ThreadPoolExecutor() as wait_executor:
                    futures = [wait_executor.submit(self._wait_for_workers, cluster_info["kubeconfig"], cluster_info["workers"], cluster_info["workers_wait_time"], cluster_name, "workers")]
                    futures.append(wait_executor.submit(self._wait_for_workers, cluster_info["kubeconfig"], platform.environment["extra_machinepool"]["replicas"], cluster_info["workers_wait_time"], cluster_name, platform.environment["extra_machinepool"]["name"])) if "extra_machinepool" in platform.environment else None
                    for future in concurrent.futures.as_completed(futures):
                        result = future.result()
                        if result[0] == "workers":
                            default_pool_workers = int(result[1])
                            if default_pool_workers == cluster_info["workers"]:
                                cluster_info["workers_ready"] = result[2] - cluster_start_time
                            else:
                                cluster_info['workers_ready'] = None
                                cluster_info['status'] = "Ready, missing workers"
                                return 1
                        else:
                            extra_pool_workers = int(result[1])
                            if "extra_machinepool" in platform.environment and extra_pool_workers == platform.environment["extra_machinepool"]["replicas"]:
                                cluster_info["extra_pool_workers_ready"] = result[2] - extra_machine_pool_start_time
                            else:
                                cluster_info["extra_pool_workers_ready"] = None
                                cluster_info['status'] = "Ready, missing extra pool workers"
                                return 1
            cluster_info['status'] = "ready"
            cluster_info["mgmt_cluster_name"] = mgmt_cluster_name
            cluster_info["metadata"]["mgmt_cluster"] = self.get_ocm_cluster_info(mgmt_cluster_name)
            # metadata['job_iterations'] = str(job_iterations) if cluster_load else 0
            # metadata['load_duration'] = load_duration if cluster_load else ""
            try:
                with open(cluster_info['path'] + "/metadata_install.json", "w") as metadata_file:
                    json.dump(cluster_info, metadata_file)
            except Exception as err:
                self.logging.error(err)
                self.logging.error(f"Failed to write metadata_install.json file located at {cluster_info['path']}")
            if self.es is not None:
                cluster_info_copy = deepcopy(cluster_info)
                del cluster_info_copy['cluster_start_time_on_mc']
                del cluster_info_copy['cluster_end_time']
                self.es.index_metadata(cluster_info_copy)
                self.logging.info("Indexing Management cluster stats")
                self.utils.cluster_load(platform, cluster_name, load="index")
            # if cluster_load:
                #     with all_clusters_installed:
                #         logging.info('Waiting for all clusters to be installed to start e2e-benchmarking execution on %s' % cluster_name)
                #         all_clusters_installed.wait()
                #     logging.info('Executing e2e-benchmarking to add load on the cluster %s with %s nodes during %s with %d iterations' % (cluster_name, str(worker_nodes), load_duration, job_iterations))
                #     _cluster_load(kubeconfig, cluster_path, cluster_name, mgmt_cluster_name, service_cluster_name, load_duration, job_iterations, es_url, mgmt_kubeconfig_path, workload_type, kube_burner_version, e2e_git_details, git_branch)
                #     logging.info('Finished execution of e2e-benchmarking workload on %s' % cluster_name)
                # if must_gather_all or create_cluster_code != 0:
                #     random_sleep = random.randint(60, 300)
                #     logging.info("Waiting %d seconds before dumping hosted cluster must-gather" % random_sleep)
                #     time.sleep(random_sleep)
                #     logging.info("Saving must-gather file of hosted cluster %s" % cluster_name)
                #     _get_must_gather(cluster_path, cluster_name)
                #     _get_mgmt_cluster_must_gather(mgmt_kubeconfig_path, path)

    def _namespace_wait(self, kubeconfig, cluster_id, cluster_name, type):
        start_time = int(datetime.datetime.utcnow().timestamp())
        self.logging.info(
            f"Capturing namespace creation time on {type} Cluster for {cluster_name}. Waiting 60 minutes until datetime.datetime.fromtimestamp(start_time + 60 * 60)"
        )
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        # Waiting 60 minutes for preflight checks to end
        while datetime.datetime.utcnow().timestamp() < start_time + 60 * 60:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting namespace creation waiting for {cluster_name} on the {type} cluster after capturing Ctrl-C")
                return 0
            (
                oc_project_code,
                oc_project_out,
                oc_project_err,
            ) = self.utils.subprocess_exec(
                "oc get projects --output json", extra_params={"env": myenv}
            )
            if oc_project_code != 0:
                self.logging.warning(
                    f"Failed to get the project list on the {type} Cluster. Retrying in 5 seconds. Waiting until {datetime.datetime.fromtimestamp(start_time + 60 * 60)}"
                )
                time.sleep(5)
            else:
                try:
                    projects_json = json.loads(oc_project_out)
                except Exception as err:
                    self.logging.warning(oc_project_out)
                    self.logging.warning(oc_project_err)
                    self.logging.warning(err)
                    self.logging.warning(
                        f"Failed to get the project list on the {type} Cluster. Retrying in 5 seconds until {datetime.datetime.fromtimestamp(start_time + 60 * 60)}"
                    )
                    time.sleep(5)
                    continue
                namespace_count = 0
                projects = projects_json.get("items", [])
                for project in projects:
                    if cluster_id in project.get("metadata", {}).get("name", ""):
                        namespace_count += 1
                if (type == "Service" and namespace_count == 2) or (
                    type == "Management" and namespace_count == 3
                ):
                    end_time = int(datetime.datetime.utcnow().timestamp())
                    self.logging.info(
                        f"Namespace for {cluster_name} created in {type} Cluster at {datetime.datetime.fromtimestamp(end_time)}"
                    )
                    return end_time
                else:
                    self.logging.warning(
                        f"Namespace for {cluster_name} not found in {type} Cluster. Retrying in 5 seconds until {datetime.datetime.fromtimestamp(start_time + 60 * 60)}"
                    )
                    time.sleep(5)
        self.logging.error(f"Failed to get namespace for {cluster_name} on the {type} cluster after 60 minutes")
        return 0

    def get_workers_ready(self, kubeconfig, cluster_name):
        super().get_workers_ready(kubeconfig, cluster_name)
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        self.logging.info(f"Getting node information for Hypershift cluster {cluster_name}")
        nodes_code, nodes_out, nodes_err = self.utils.subprocess_exec("oc get nodes -o json", extra_params={"env": myenv, "universal_newlines": True}, log_output=False)
        try:
            nodes_json = json.loads(nodes_out)
        except Exception as err:
            self.logging.debug(f"Cannot load command result for cluster {cluster_name}")
            self.logging.debug(err)
            return 0
        nodes = nodes_json["items"] if "items" in nodes_json else []
        status = []
        for node in nodes:
            nodepool = node.get("metadata", {}).get("labels", {}).get("hypershift.openshift.io/nodePool", "")
            if "workers" in nodepool:
                conditions = node.get("status", {}).get("conditions", [])
                for condition in conditions:
                    if "type" in condition and condition["type"] == "Ready":
                        status.append(condition["status"])
        status_list = {i: status.count(i) for i in status}
        ready_nodes = status_list["True"] if "True" in status_list else 0
        return ready_nodes


class HypershiftArguments(RosaArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--create-vpcs", action="store_true", help="Create a VPC for each Hosted Cluster")
        parser.add_argument("--clusters-per-vpc", action=EnvDefault, env=environment, envvar="HCP_BURNER_CLUSTERS_PER_VPC", help="Number of HC to create on each VPC", type=int, default=1, choices=range(1, 11))
        parser.add_argument("--terraform-retry", type=int, default=5, help="Number of retries when executing terraform commands")
        parser.add_argument("--service-cluster", action=EnvDefault, env=environment, envvar="HCP_BURNER_HYPERSHIFT_SERVICE_CLUSTER", help="Service Cluster Used to create the Hosted Clusters")
        parser.add_argument("--delete-vpcs", action="store_true", help="Delete all VPC after cleanup")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Rosa:Hypershift")))
            parser.set_defaults(**defaults)
