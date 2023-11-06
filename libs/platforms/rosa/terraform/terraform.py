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
import concurrent.futures


from libs.platforms.rosa.rosa import Rosa
from libs.platforms.rosa.rosa import RosaArguments


class Terraform(Rosa):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["commands"].append("terraform")

        self.logging.info("Parameter --workers will be ignored on terraform subplatform. OCM Terraform module is fixed to 2 workers")
        self.environment["workers"] = "2"

        if self.environment['cluster_count'] % arguments['clusters_per_apply'] == 0:
            self.logging.debug(str(self.environment['cluster_count'] % arguments['clusters_per_apply']))
            self.logging.info(str(arguments['clusters_per_apply']) + " clusters will be installed on each Terraform Apply")
            self.environment['clusters_per_apply'] = arguments['clusters_per_apply']
            self.environment['clusters_per_apply_count'] = self.environment['cluster_count'] / self.environment['clusters_per_apply']
        else:
            self.logging.debug(str(self.environment['cluster_count'] % arguments['clusters_per_apply']))
            self.logging.error("--cluster-count (" + str(self.environment['cluster_count']) + ") parameter must be divisible by --clusters-per-apply (" + str(arguments['clusters_per_apply']) + ")")
            sys.exit("Exiting...")

    def initialize(self):
        super().initialize()

        if not os.path.exists(self.environment['path'] + "/terraform"):
            shutil.copytree(sys.path[0] + "/libs/platforms/rosa/terraform/files", self.environment['path'] + "/terraform")

        self.logging.info("Initializing Terraform with: terraform init")
        terraform_code, terraform_out, terraform_err = self.utils.subprocess_exec("terraform init", self.environment["path"] + "/terraform/terraform-init.log", {"cwd": self.environment["path"] + "/terraform"})
        if terraform_code != 0:
            self.logging.error(f"Failed to initialize terraform. Check {self.environment['path']}/terraform/terraform-init.log for more information")
            sys.exit("Exiting...")

        terraform_code, terraform_out, terraform_err = self.utils.subprocess_exec("terraform init", self.environment["path"] + "/terraform/oidc_provider/terraform-init.log", {"cwd": self.environment["path"] + "/terraform/oidc_provider"})
        if terraform_code != 0:
            self.logging.error(f"Failed to initialize terraform. Check {self.environment['path']}/terraform/oidc_provider/terraform-init.log for more information")
            sys.exit("Exiting...")

    def platform_cleanup(self, platform=""):
        super().platform_cleanup(platform)
        self.destroy_tf_template(platform, tf_module="oidc")

    def _oidc_tf_template(self, action, tf_path, myenv):
        code, out, err = self.utils.subprocess_exec("terraform " + action + " --auto-approve -state=" + tf_path + "/terraform_oidc.tfstate ", tf_path + "/terraform_oidc_" + action.replace(" ", "") + ".log", {"cwd": self.environment['path'] + "/terraform/oidc_provider", 'preexec_fn': self.utils.disable_signals, "env": myenv})
        return code, out, err

    # creates templates based on the clusters_per_apply
    # and apply them at given interval without wait for it to complete
    def apply_tf_template(self, platform):
        loop_counter = 0
        while loop_counter < platform.environment["clusters_per_apply_count"]:
            tf_counter = 0
            self.logging.debug(platform.environment["clusters"])
            if self.utils.force_terminate:
                loop_counter += 1
            else:
                cluster_workers = int(platform.environment["workers"])

                tf_name = platform.environment["cluster_name_seed"]

                try:
                    tf_path = platform.environment["path"] + "/" + "TF_" + tf_name + "-" + str(loop_counter * self.environment['clusters_per_apply']).zfill(4)
                    os.mkdir(tf_path)

                    myenv = os.environ.copy()
                    myenv["TF_VAR_token"] = self.environment["ocm_token"]
                    myenv["TF_VAR_cloud_region"] = self.environment['aws']['region']
                    myenv["TF_VAR_url"] = self.environment["ocm_url"]
                    myenv["TF_VAR_account_role_prefix"] = 'ManagedOpenShift'
                    myenv["TF_VAR_cluster_name"] = tf_name
                    myenv["TF_VAR_replicas"] = str(cluster_workers)
                    myenv["TF_VAR_operator_role_prefix"] = tf_name + "-" + str(loop_counter)
                    myenv["TF_VAR_clusters_per_apply"] = str(self.environment['clusters_per_apply'])
                    myenv["TF_VAR_loop_factor"] = str((loop_counter * self.environment['clusters_per_apply']))

                    # additional env for oidc_provider template
                    myenv["TF_VAR_managed"] = "true"

                    self.logging.info(f"Applying OIDC template to create oidc_provider for cluster seed {tf_name} looping {loop_counter + 1}")
                    terraform_oidc_apply_code, terraform_oidc_apply_out, terraform_oidc_apply_err = self._oidc_tf_template("apply", tf_path, myenv)
                    if terraform_oidc_apply_code != 0:
                        self.logging.error(f"OIDC with seed {tf_name} looping {loop_counter + 1} terraform apply failed")
                        self.logging.debug(terraform_oidc_apply_out)
                        return 1
                    else:
                        self.logging.info(f"Applied OIDC template successfully for cluster seed {tf_name} looping {loop_counter + 1}")
                        with open(tf_path + "/terraform_oidc.tfstate", "r") as terraform_state:
                            json_output = json.load(terraform_state)
                        oidc_id = json_output["outputs"]["id"]["value"]

                    # Passing new OIDC ID to the cluster template
                    myenv["TF_VAR_oidc_config_id"] = oidc_id

                    self.logging.info(f"Applying template to create {platform.environment['clusters_per_apply']} with cluster seed {tf_name} looping {loop_counter + 1}")
                    terraform_plan_code, terraform_plan_out, terraform_plan_err = self.utils.subprocess_exec("terraform plan -out " + tf_path + "/" + tf_name + ".tfplan", tf_path + "/terraform_plan.log", {"cwd": self.environment['path'] + "/terraform", "env": myenv})
                    if terraform_plan_code != 0:
                        self.logging.error(f"Clusters with seed {tf_name} looping {loop_counter + 1} terraform plan failed")
                        self.logging.debug(terraform_plan_out)
                        return 1
                    else:
                        self.logging.info(f"Trying to install clusters with TF template {tf_name} looping {loop_counter + 1} with {cluster_workers} workers up to 5 times using terraform provider")
                        trying = 0
                        while trying <= 5:
                            if self.utils.force_terminate:
                                self.logging.error(f"Exiting clusters creation for {tf_name} looping {loop_counter + 1} after capturing Ctrl-C")
                                return 0
                            trying += 1
                            cluster_apply_time = int(datetime.datetime.utcnow().timestamp())
                            terraform_apply_code, terraform_apply_out, terraform_apply_err = self.utils.subprocess_exec("terraform apply -state=" + tf_path + "/terraform.tfstate " + tf_path + "/" + tf_name + ".tfplan", tf_path + "/terraform_apply-" + str(trying) + ".log", {"cwd": self.environment['path'] + "/terraform", 'preexec_fn': self.utils.disable_signals, "env": myenv})
                            if terraform_apply_code != 0:
                                self.logging.debug(terraform_apply_out)
                                self.logging.debug(terraform_apply_err)
                                if trying <= 5:
                                    self.logging.warning(f"Try: {trying}/5. Clusters with seed {tf_name} looping {loop_counter + 1} installation failed, retrying in 15 seconds")
                                    time.sleep(15)
                                else:
                                    self.logging.error(f"Clusters with seed {tf_name} looping {loop_counter + 1} installation failed after 5 retries")
                                    self.logging.debug(terraform_apply_out)
                                    self.logging.debug(terraform_apply_err)
                                    return 1
                            else:
                                break

                except Exception as err:
                    self.logging.error(f"Failed to apply with cluster seed {tf_name} looping {loop_counter + 1}")
                    self.logging.error(err)
                    return 1
                while tf_counter < platform.environment["clusters_per_apply"]:
                    cluster_name = platform.environment["cluster_name_seed"] + "-" + str((loop_counter * self.environment['clusters_per_apply']) + (tf_counter + 1)).zfill(4)
                    platform.environment["clusters"][cluster_name]["cluster_apply_time"] = cluster_apply_time
                    tf_counter += 1
                if platform.environment["delay_between_batch"] is None:
                    time.sleep(1)
                else:
                    time.sleep(platform.environment["delay_between_batch"])                
            loop_counter += 1
        return 0

    # uses created templates based on the clusters_per_apply
    # and destroy them at given interval without wait for it to complete
    def destroy_tf_template(self, platform, tf_module="cluster"):
        loop_counter = 0
        while loop_counter < platform.environment["clusters_per_apply_count"]:
            self.logging.debug(platform.environment["clusters"])
            if self.utils.force_terminate:
                loop_counter += 1
            else:
                cluster_workers = int(platform.environment["workers"])

                tf_name = platform.environment["cluster_name_seed"]

                try:

                    tf_path = platform.environment["path"] + "/" + "TF_" + tf_name + "-" + str(loop_counter * self.environment['clusters_per_apply']).zfill(4)
                    if not os.path.exists(tf_path):
                        os.mkdir(tf_path)

                    myenv = os.environ.copy()
                    myenv["TF_VAR_token"] = self.environment["ocm_token"]
                    myenv["TF_VAR_cloud_region"] = self.environment['aws']['region']
                    myenv["TF_VAR_url"] = self.environment["ocm_url"]
                    myenv["TF_VAR_account_role_prefix"] = 'ManagedOpenShift'
                    myenv["TF_VAR_cluster_name"] = tf_name
                    myenv["TF_VAR_replicas"] = str(cluster_workers)
                    myenv["TF_VAR_operator_role_prefix"] = tf_name + "-" + str(loop_counter)
                    myenv["TF_VAR_clusters_per_apply"] = str(self.environment['clusters_per_apply'])
                    myenv["TF_VAR_loop_factor"] = str((loop_counter * self.environment['clusters_per_apply']))

                    if tf_module == "oidc":
                        # additional env for oidc_provider template
                        myenv["TF_VAR_managed"] = "true"

                        self.logging.info(f"Destroying OIDC template to delete oidc_provider for cluster seed {tf_name} looping {loop_counter + 1}")
                        terraform_oidc_destroy_code, terraform_oidc_destroy_out, terraform_oidc_destroy_err = self._oidc_tf_template("apply -destroy", tf_path, myenv)
                        if terraform_oidc_destroy_code != 0:
                            self.logging.error(f"OIDC with seed {tf_name} looping {loop_counter + 1} terraform destroy failed")
                            self.logging.debug(terraform_oidc_destroy_out)
                            return 1

                    else:
                        self.logging.info(f"Deleting Clusters with seed {tf_name} looping {loop_counter + 1} on Rosa Platform using terraform")
                        trying = 0
                        while trying <= 5:
                            if self.utils.force_terminate:
                                self.logging.error(f"Exiting clusters deletion for {tf_name} looping {loop_counter + 1} after capturing Ctrl-C")
                                return 0
                            trying += 1
                            cleanup_code, cleanup_out, cleanup_err = self.utils.subprocess_exec("terraform apply -destroy -state=" + tf_path + "/terraform.tfstate --auto-approve", tf_path + "/cleanup-" + str(trying) + ".log", {"cwd": self.environment['path'] + "/terraform", 'preexec_fn': self.utils.disable_signals, "env": myenv})
                            if cleanup_code != 0:
                                self.logging.debug(f"Clusters Cleanup with seed {tf_name} looping {loop_counter + 1} is failed")
                                self.logging.debug(cleanup_out)
                                self.logging.debug(cleanup_err)
                                if trying <= 5:
                                    self.logging.warning(f"Try: {trying}/5. Clusters with seed {tf_name} looping {loop_counter + 1} deletion failed, retrying in 15 seconds")
                                    time.sleep(15)
                                else:
                                    self.logging.error(f"Clusters with seed {tf_name} looping {loop_counter + 1} deletion failed after 5 retries")
                                    self.logging.debug(cleanup_out)
                                    self.logging.debug(cleanup_err)
                                    return 1
                            else:
                                break

                except Exception as err:
                    self.logging.error(f"Failed to apply with cluster seed {tf_name} looping {loop_counter + 1}")
                    self.logging.error(err)
                    return 1
                if platform.environment["delay_between_cleanup"] is None:
                    time.sleep(1)
                else:
                    time.sleep(platform.environment["delay_between_cleanup"])
            loop_counter += 1
        return 0

    # Cluster deletion will be initiated by destroy_tf_template
    # this function waits and verifies the deletions
    # No actual delete logic in this function unlike other subplatform
    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)
        retry_loop = 0
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["install_method"] = "terraform"
        cluster_info["per_template_count"] = platform.environment['clusters_per_apply']
        cluster_info["tf_count"] = platform.environment['clusters_per_apply_count']
        cluster_info["total_count"] = platform.environment['cluster_count']
        self.logging.info(f"Checking uninstall log for cluster {cluster_name}")

        while retry_loop <= 600:  # 1hr timeout
            retry_loop += 1
            cluster_delete_start_time = int(datetime.datetime.utcnow().timestamp())
            index_time = datetime.datetime.utcnow().isoformat()
            watch_code, watch_out, watch_err = self.utils.subprocess_exec("rosa logs uninstall -c " + cluster_name + " --watch", cluster_info["path"] + "/cleanup.log", {'preexec_fn': self.utils.disable_signals})
            if watch_code != 0:
                if retry_loop <= 600:
                    self.logging.debug(f"ROSA cluster uninstall log for {cluster_name} is not available yet, retrying..")
                    self.logging.debug(watch_out)
                    time.sleep(6)
                else:
                    cluster_info['status'] = "not deleted"
                    self.logging.debug(watch_out)
                    self.logging.error(watch_err)
                    return 1
            else:
                break

        cluster_delete_end_time = int(datetime.datetime.utcnow().timestamp())
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

        cluster_end_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["destroy_duration"] = cluster_delete_end_time - cluster_delete_start_time
        cluster_info["destroy_all_duration"] = cluster_end_time - cluster_delete_start_time
        try:
            with open(cluster_info['path'] + "/metadata_destroy.json", "w") as metadata_file:
                json.dump(cluster_info, metadata_file)
        except Exception as err:
            self.logging.error(err)
            self.logging.error(f"Failed to write metadata_install.json file located at {cluster_info['path']}")
        if self.es is not None:
            cluster_info["timestamp"] = index_time
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

    # Cluster creation will be initiated by apply_tf_template
    # this function waits and verifies the creation
    # No actual create logic in this function unlike other subplatform
    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)
        retry_loop = 0
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["install_method"] = "terraform"
        cluster_info["per_template_count"] = platform.environment['clusters_per_apply']
        cluster_info["tf_count"] = platform.environment['clusters_per_apply_count']
        cluster_info["total_count"] = platform.environment['cluster_count']
        self.logging.info(f"Creating cluster {cluster_info['index']} on ROSA with name {cluster_name} and {cluster_info['workers']} workers")
        cluster_info["path"] = platform.environment["path"] + "/" + cluster_name
        os.mkdir(cluster_info["path"])
        self.logging.debug("Output directory set to %s" % cluster_info["path"])

        while retry_loop <= 60:  # 10 min timeout
            retry_loop += 1
            cluster_start_time = int(datetime.datetime.utcnow().timestamp())
            index_time = datetime.datetime.utcnow().isoformat()
            status_code, status_out, status_err = self.utils.subprocess_exec("rosa describe cluster -c " + cluster_name + " -o json", extra_params={"universal_newlines": True}, log_output=False)
            if status_code != 0:
                if retry_loop <= 60:
                    self.logging.debug(f"ROSA cluster {cluster_name} is not available yet, retrying..")
                    self.logging.debug(status_out)
                    time.sleep(5)
                else:
                    cluster_info['status'] = "not ready"
                    self.logging.debug(status_out)
                    self.logging.error(status_err)
                    return 1
            else:
                cluster_listed_time = int(datetime.datetime.utcnow().timestamp())
                preflight_ch = self._preflight_wait(cluster_name, cluster_name)
                cluster_info["preflight_checks"] = preflight_ch
                break

        watch_code, watch_out, watch_err = self.utils.subprocess_exec("rosa logs install -c " + cluster_name + " --watch", cluster_info["path"] + "/installation.log", {'preexec_fn': self.utils.disable_signals})
        status_code, status_out, status_err = self.utils.subprocess_exec("rosa describe cluster -c " + cluster_name + " -o json", extra_params={"universal_newlines": True})
        current_status = json.loads(status_out)["state"]
        if watch_code != 0 or current_status != "ready":
            cluster_info['status'] = "not installed"
            return 1
        else:
            cluster_info['status'] = "installed"
            cluster_end_time = int(datetime.datetime.utcnow().timestamp())
            # Getting againg metadata to update the cluster status
            cluster_info["metadata"] = self.get_metadata(cluster_name)
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
            cluster_info['status'] = "ready"
            cluster_apply_time = int(platform.environment["clusters"][cluster_name]["cluster_apply_time"])
            cluster_info["apply_duration"] = cluster_listed_time - cluster_apply_time            
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
        parser.add_argument("--clusters-per-apply", type=int, default=1, help="Number of clusters to install on each terraform apply")
#        parser.add_argument("--service-cluster", action=EnvDefault, env=environment, envvar="ROSA_BURNER_HYPERSHIFT_SERVICE_CLUSTER", help="Service Cluster Used to create the Hosted Clusters")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Rosa:Hypershift")))
            parser.set_defaults(**defaults)
