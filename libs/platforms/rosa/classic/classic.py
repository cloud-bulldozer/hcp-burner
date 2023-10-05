#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import os
import time
import datetime
import math
import shutil
import concurrent.futures
import configparser

from libs.platforms.rosa.rosa import Rosa
from libs.platforms.rosa.rosa import RosaArguments


class Classic(Rosa):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

    def initialize(self):
        super().initialize()

    def platform_cleanup(self):
        super().platform_cleanup()

    def watcher(self):
        super().watcher()

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["clusters"] = self.environment["cluster_count"]
        cluster_info["install_method"] = self.environment["install_method"]
        self.logging.info(f"Creating cluster {cluster_info['index']} with name {cluster_name} and {cluster_info['workers']} workers")
        cluster_info["path"] = platform.environment["path"] + "/" + cluster_name
        os.mkdir(cluster_info["path"])
        self.logging.debug("Attempting cluster installation")
        self.logging.debug("Output directory set to %s" % cluster_info["path"])
        cmd_path = cluster_info["path"]
        cmd_env = os.environ.copy()
        if self.environment["install_method"] == "rosa":
            cluster_cmd = ["rosa", "create", "cluster", "--cluster-name", cluster_name, "--replicas", str(cluster_info["workers"]), "--sts", "--mode", "auto", "-y", "--output", "json", "--oidc-config-id", platform.environment["oidc_config_id"]]
        else:
            cmd_path = cmd_path + "/terraform"
            shutil.copytree(sys.path[0] + "/libs/platforms/rosa/classic/terraform", cmd_path)
            cmd_env["TF_VAR_token"] = self.environment["ocm_token"]
            cmd_env["TF_VAR_availability_zones"] = "['"+ os.environ["AWS_REGION"] +"a']" # us-west-2a
            cmd_env["TF_VAR_cloud_region"] = os.environ["AWS_REGION"]
            cmd_env["TF_VAR_url"] = self.environment["ocm_url"]
            cmd_env["TF_VAR_operator_role_prefix"] = cluster_name
            cmd_env["TF_VAR_account_role_prefix"] = "ManagedOpenShift"
            init_code, init_out, init_err = self.utils.subprocess_exec("terraform init", cluster_info["path"] + "/installation.log", {'preexec_fn': self.utils.disable_signals, 'cwd': cmd_path, 'env': cmd_env})
            plan_code, plan_out, plan_err = self.utils.subprocess_exec("terraform plan -out rosa-cluster.tfplan", cluster_info["path"] + "/installation.log", {'preexec_fn': self.utils.disable_signals, 'cwd': cmd_path, 'env': cmd_env})
            cluster_cmd = ["terraform", "apply", "rosa-cluster.tfplan", "--auto-approve"]
            # cmd_env["TF_VAR_openshift_version"] = 
        if platform.environment["wildcard_options"]:
            for param in platform.environment["wildcard_options"].split():
                cluster_cmd.append(param)
        if self.environment["common_operator_roles"]:
            cluster_cmd.append("--operator-roles-prefix")
            cluster_cmd.append(self.environment["common_operator_roles"])
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())
        self.logging.info(f"Trying to install cluster {cluster_name} with {cluster_info['workers']} workers up to 5 times")
        trying = 0
        while trying <= 5:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting cluster creation for {cluster_name} after capturing Ctrl-C")
                return 0
            self.logging.info("Cluster Create Command:")
            self.logging.info(cluster_cmd)
            (create_cluster_code, create_cluster_out, create_cluster_err) = self.utils.subprocess_exec(" ".join(str(x) for x in cluster_cmd), cluster_info["path"] + "/installation.log", {'preexec_fn': self.utils.disable_signals, 'cwd': cmd_path, 'env': cmd_env})
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

        cluster_info['status'] = "Installing"
        self.logging.info(f"Cluster {cluster_name} installation started on the {trying} try")
        cluster_info["metadata"] = self.get_metadata(cluster_name)
        cluster_info["install_try"] = trying
        watch_code, watch_out, watch_err = self.utils.subprocess_exec("rosa logs install -c " + cluster_name + " --watch", cluster_info["path"] + "/installation.log", {'preexec_fn': self.utils.disable_signals})
        if watch_code != 0:
            cluster_info['status'] = "not ready"
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
            try:
                with open(cluster_info['path'] + "/metadata_install.json", "w") as metadata_file:
                    json.dump(cluster_info, metadata_file)
            except Exception as err:
                self.logging.error(err)
                self.logging.error(f"Failed to write metadata_install.json file located at {cluster_info['path']}")
            if self.es is not None:
                cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
                self.es.index_metadata(cluster_info)

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["install_method"] = self.environment["install_method"]
        self.logging.info(f"Deleting cluster {cluster_name}")
        if self.environment["install_method"] == "rosa":
            cleanup_code, cleanup_out, cleanup_err = self.utils.subprocess_exec("rosa delete cluster -c " + cluster_name + " -y --watch", cluster_info["path"] + "/cleanup.log", {'preexec_fn': self.utils.disable_signals})
        else:
            cmd_path = cluster_info["path"] + "/" + "terraform"
            cmd_env = os.environ.copy()
            cmd_env["TF_VAR_token"] = self.environment["ocm_token"]
            cmd_env["TF_VAR_availability_zones"] = "['"+ os.environ["AWS_REGION"] +"a']" # us-west-2a
            cmd_env["TF_VAR_cloud_region"] = os.environ["AWS_REGION"]
            cmd_env["TF_VAR_url"] = self.environment["ocm_url"]
            cmd_env["TF_VAR_operator_role_prefix"] = cluster_name
            cmd_env["TF_VAR_account_role_prefix"] = "ManagedOpenShift"
            cleanup_code, cleanup_out, cleanup_err = self.utils.subprocess_exec("terraform destroy --auto-approve", cluster_info["path"] + "/cleanup.log", {'preexec_fn': self.utils.disable_signals, 'cwd': cmd_path, 'env': cmd_env})
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
                self.logging.debug(
                    f"Destroying STS associated resources of cluster name: {cluster_name}"
                )
                (operators_code, operators_out, operators_err) = self.utils.subprocess_exec("rosa delete operator-roles --prefix " + cluster_name + " -m auto -y", cluster_info["path"] + "/cleanup.log", {'preexec_fn': self.utils.disable_signals})
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
            cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
            self.es.index_metadata(cluster_info)

class ClassicArguments(RosaArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--install-method", action=EnvDefault, env=environment, envvar="ROSA_INSTALL_METHOD", type=str, default="rosa")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Rosa:Classic")))
            parser.set_defaults(**defaults)
