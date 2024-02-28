#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import os
import time
import datetime
# import math
import shutil
import configparser

from libs.platforms.rosa.rosa import Rosa
from libs.platforms.rosa.rosa import RosaArguments


class Terraform(Rosa):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["commands"].append("terraform")

        self.logging.info("Parameter --workers will be ignored on terraform subplatform. OCM Terraform module is fixed to 2 workers")
        self.environment["workers"] = "2"

        # if self.environment['cluster_count'] % arguments['clusters_per_apply'] == 0:
        #     self.logging.debug(str(self.environment['cluster_count'] % arguments['clusters_per_apply']))
        #     self.logging.info(str(arguments['clusters_per_apply']) + " clusters will be installed on each Terraform Apply")
        #     self.environment['clusters_per_apply'] = arguments['clusters_per_apply']
        #     self.environment['cluster_count'] = self.environment['cluster_count'] / self.environment['clusters_per_apply']
        # else:
        #     self.logging.debug(str(self.environment['cluster_count'] % arguments['clusters_per_apply']))
        #     self.logging.error("--cluster-count (" + str(self.environment['cluster_count']) + ") parameter must be divisible by --clusters-per-apply (" + str(arguments['clusters_per_apply']) + ")")
        #     sys.exit("Exiting...")

    def initialize(self):
        super().initialize()

        shutil.copytree(sys.path[0] + "/libs/platforms/rosa/terraform/files", self.environment['path'] + "/terraform")

        self.logging.info("Initializing Terraform with: terraform init")
        terraform_code, terraform_out, terraform_err = self.utils.subprocess_exec("terraform init", self.environment["path"] + "/terraform/terraform-init.log", {"cwd": self.environment["path"] + "/terraform"})
        if terraform_code != 0:
            self.logging.error(f"Failed to initialize terraform. Check {self.environment['path']}/terraform/init.log for more information")
            sys.exit("Exiting...")

    def platform_cleanup(self):
        super().platform_cleanup()

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)

        myenv = os.environ.copy()
        myenv["TF_VAR_token"] = self.environment["ocm_token"]
        myenv["TF_VAR_cloud_region"] = self.environment['aws']['region']
        myenv["TF_VAR_url"] = self.environment["ocm_url"]
        myenv["TF_VAR_account_role_prefix"] = 'ManagedOpenShift'
        myenv["TF_VAR_cluster_name"] = cluster_name
        myenv["TF_VAR_operator_role_prefix"] = cluster_name
#        myenv["TF_VAR_clusters_per_apply"] = str(self.environment['clusters_per_apply'])

        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["install_method"] = "terraform"
        self.logging.info(f"Deleting cluster {cluster_name} on Rosa Platform using terraform")
        cleanup_code, cleanup_out, cleanup_err = self.utils.subprocess_exec("terraform apply -destroy -state=" + cluster_info['path'] + "/terraform.tfstate --auto-approve", cluster_info["path"] + "/cleanup.log", {"cwd": self.environment['path'] + "/terraform", 'preexec_fn': self.utils.disable_signals, "env": myenv})
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
            else:
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
            cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
            self.es.index_metadata(cluster_info)

    def get_workers_ready(self, kubeconfig, cluster_name):
        super().get_workers_ready(kubeconfig, cluster_name)
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        self.logging.info(f"Getting node information for Terraform installed cluster {cluster_name}")
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
            labels = node.get("metadata", {}).get("labels", {})
            if "node-role.kubernetes.io/worker" in labels and "node-role.kubernetes.io/control-plane" not in labels and "node-role.kubernetes.io/infra" not in labels:
                conditions = node.get("status", {}).get("conditions", [])
                for condition in conditions:
                    if "type" in condition and condition["type"] == "Ready":
                        status.append(condition["status"])
        status_list = {i: status.count(i) for i in status}
        ready_nodes = status_list["True"] if "True" in status_list else 0
        return ready_nodes

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["install_method"] = "terraform"
        self.logging.info(f"Creating cluster {cluster_info['index']} on ROSA with name {cluster_name} and {cluster_info['workers']} workers")
        cluster_info["path"] = platform.environment["path"] + "/" + cluster_name
        os.mkdir(cluster_info["path"])
        self.logging.debug("Attempting cluster installation")
        self.logging.debug("Output directory set to %s" % cluster_info["path"])

        myenv = os.environ.copy()
        myenv["TF_VAR_token"] = self.environment["ocm_token"]
        myenv["TF_VAR_cloud_region"] = self.environment['aws']['region']
        myenv["TF_VAR_url"] = self.environment["ocm_url"]
        myenv["TF_VAR_account_role_prefix"] = 'ManagedOpenShift'
        myenv["TF_VAR_cluster_name"] = cluster_name
        myenv["TF_VAR_operator_role_prefix"] = cluster_name
#        myenv["TF_VAR_clusters_per_apply"] = str(self.environment['clusters_per_apply'])

        terraform_plan_code, terraform_plan_out, terraform_plan_err = self.utils.subprocess_exec("terraform plan -out " + cluster_info['path'] + "/" + cluster_name + ".tfplan", cluster_info["path"] + "/terraform_plan.log", {"cwd": self.environment['path'] + "/terraform", "env": myenv})
        if terraform_plan_code != 0:
            cluster_end_time = int(datetime.datetime.utcnow().timestamp())
            cluster_info["status"] = "Not Installed"
            self.logging.error(f"Cluster {cluster_name} terraform plan failed")
            self.logging.debug(terraform_plan_out)
            return 1
        else:
            self.logging.info(f"Trying to install cluster {cluster_name} with {cluster_info['workers']} workers up to 5 times using terraform provider")
            trying = 0
            while trying <= 5:
                cluster_start_time = int(datetime.datetime.utcnow().timestamp())
                if self.utils.force_terminate:
                    self.logging.error(f"Exiting cluster creation for {cluster_name} after capturing Ctrl-C")
                    return 0
                trying += 1
                terraform_apply_code, terraform_apply_out, terraform_apply_err = self.utils.subprocess_exec("terraform apply -state=" + cluster_info['path'] + "/terraform.tfstate " + cluster_info['path'] + "/" + cluster_name + ".tfplan", cluster_info["path"] + "/terraform_apply.log", {"cwd": self.environment['path'] + "/terraform", 'preexec_fn': self.utils.disable_signals, "env": myenv})
                if terraform_apply_code != 0:
                    cluster_info["install_try"] = trying
                    self.logging.debug(terraform_apply_out)
                    self.logging.debug(terraform_apply_err)
                    if trying <= 5:
                        self.logging.warning(f"Try: {trying}/5. Cluster {cluster_name} installation failed, retrying in 15 seconds")
                        time.sleep(15)
                    else:
                        cluster_end_time = int(datetime.datetime.utcnow().timestamp())
                        cluster_info["status"] = "Not Installed"
                        self.logging.error(f"Cluster {cluster_name} installation failed after 5 retries")
                        self.logging.debug(terraform_apply_out)
                        self.logging.debug(terraform_apply_err)
                        return 1
                else:
                    cluster_end_time = int(datetime.datetime.utcnow().timestamp())
                    index_time = datetime.datetime.utcnow().isoformat()
                    break

        cluster_info['status'] = "installed"
        self.logging.info(f"Cluster {cluster_name} installation finished on the {trying} try")
        cluster_info["metadata"] = self.get_metadata(cluster_name)
        cluster_info["install_try"] = trying
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
        if cluster_info["workers_wait_time"]:
            workers_ready = self._wait_for_workers(cluster_info["kubeconfig"], cluster_info["workers"], cluster_info["workers_wait_time"], cluster_name, "workers")
            if workers_ready[1] == cluster_info["workers"]:
                cluster_info["workers_ready"] = workers_ready[2] - cluster_start_time
            else:
                cluster_info['workers_ready'] = None
                cluster_info['status'] = "Ready, missing workers"
                return 1
        cluster_info['status'] = "ready"
        try:
            with open(cluster_info['path'] + "/metadata_install.json", "w") as metadata_file:
                json.dump(cluster_info, metadata_file)
        except Exception as err:
            self.logging.error(err)
            self.logging.error(f"Failed to write metadata_install.json file located at {cluster_info['path']}")
        if self.es is not None:
            cluster_info["timestamp"] = index_time
            self.es.index_metadata(cluster_info)

    def _wait_for_workers(self, kubeconfig, worker_nodes, wait_time, cluster_name, machinepool_name):
        self.logging.info(f"Waiting {wait_time} minutes for {worker_nodes} workers to be ready on {machinepool_name} machinepool on {cluster_name}")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        result = [machinepool_name]
        starting_time = datetime.datetime.utcnow().timestamp()
        self.logging.debug(f"Waiting {wait_time} minutes for nodes to be Ready on cluster {cluster_name} until {datetime.datetime.fromtimestamp(starting_time + wait_time * 60)}")
        while datetime.datetime.utcnow().timestamp() < starting_time + wait_time * 60:
            # if force_terminate:
            #     logging.error("Exiting workers waiting on the cluster %s after capturing Ctrl-C" % cluster_name)
            #     return []
            self.logging.info("Getting node information for cluster %s" % cluster_name)
            nodes_code, nodes_out, nodes_err = self.utils.subprocess_exec("oc get nodes -o json", extra_params={"env": myenv, "universal_newlines": True})
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

            ready_nodes = 0
            for node in nodes:
                labels = node.get("metadata", {}).get("labels", {})
                if "node-role.kubernetes.io/worker" in labels and "node-role.kubernetes.io/control-plane" not in labels and "node-role.kubernetes.io/infra" not in labels:
                    conditions = node.get("status", {}).get("conditions", [])
                    for condition in conditions:
                        if "type" in condition and condition["type"] == "Ready":
                            ready_nodes += 1
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


class TerraformArguments(RosaArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
#        EnvDefault = self.EnvDefault

        parser.add_argument("--terraform-retry", type=int, default=5, help="Number of retries when executing terraform commands")
#        parser.add_argument("--clusters-per-apply", type=int, default=1, help="Number of clusters to install on each terraform apply")
#        parser.add_argument("--service-cluster", action=EnvDefault, env=environment, envvar="HCP_BURNER_HYPERSHIFT_SERVICE_CLUSTER", help="Service Cluster Used to create the Hosted Clusters")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Rosa:Terraform")))
            parser.set_defaults(**defaults)
