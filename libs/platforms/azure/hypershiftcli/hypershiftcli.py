#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import re
import os
import time
import datetime
import configparser
import base64
import concurrent

from libs.platforms.azure.azure import Azure
from libs.platforms.azure.azure import AzureArguments


class Hypershiftcli(Azure):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        pattern = re.compile(r"^(\d+)(,\s*\d+)*$")
        if arguments["workers"].isdigit() and int(arguments["workers"]) % 3 != 0:
            self.logging.error(f"Invalid value ({arguments['workers']}) for parameter  `--workers`. If digit, it must be divisible by 3'")
            sys.exit("Exiting...")
        elif bool(pattern.match(arguments["workers"])):
            for num in arguments["workers"].split(","):
                if int(num) < 3 or int(num) % 3 != 0:
                    self.logging.error(f"Invalid value ({num}) for parameter `--workers`. If list, all values must be divisible by 3")
                    sys.exit("Exiting...")

        self.environment["commands"].append("kubectl")
        self.environment["workers"] = arguments["workers"]
        self.environment["mc_kubeconfig"] = arguments["mc_kubeconfig"]
        self.environment["mc_resource_group"] = arguments["mc_az_resource_group"]
        self.environment['mgmt_cluster_name'] = arguments["mc_cluster_name"]

    def initialize(self):
        super().initialize()
        # Verify access to the MC Cluster
        self.logging.info(f"Verifying access to the MC Cluster using {self.environment['mc_kubeconfig']} file...")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment['mc_kubeconfig']
        hosted_code, hosted_out, hosted_err = self.utils.subprocess_exec("kubectl get hostedclusters -A", extra_params={"env": myenv, "universal_newlines": True}, log_output=False)
        if hosted_code != 0:
            self.logging.error(f"Failed to list hosted clusters using {self.environment['mc_kubeconfig']} file")
            sys.exit("Exiting...")
        else:
            self.logging.info(f"Access to MC cluster {self.environment['mgmt_cluster_name']} verified using {self.environment['mc_kubeconfig']} file")

    def platform_cleanup(self):
        super().platform_cleanup()

    def watcher(self):
        super().watcher()
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment['mc_kubeconfig']
        self.logging.info(f"Watcher started on {self.environment['platform']}")
        self.logging.info(f"Getting status every {self.environment['watcher_delay']}")
        self.logging.info(f"Expected Clusters: {self.environment['cluster_count']}")
        self.logging.info(f"Manually terminate watcher creating the file {self.environment['path']}/terminate_watcher")
        file_path = os.path.join(self.environment["path"], "terminate_watcher")
        if os.path.exists(file_path):
            os.remove(file_path)
        while not self.utils.force_terminate:
            self.logging.debug(self.environment['clusters'])
            if os.path.isfile(os.path.join(self.environment["path"], "terminate_watcher")):
                self.logging.warning("Watcher has been manually set to terminate")
                break
            cluster_list_code, cluster_list_out, cluster_list_err = self.utils.subprocess_exec("oc get hostedcluster -n clusters -o json", extra_params={"env": myenv, "universal_newlines": True})
            current_cluster_count = 0
            installed_clusters = 0
#            clusters_with_all_workers = 0
            state = {}
            error = []
            try:
                oc_list_clusters = json.loads(cluster_list_out)
            except ValueError as err:
                self.logging.error("Failed to get clusters list: %s" % err)
                self.logging.error(cluster_list_out)
                self.logging.error(cluster_list_err)
                oc_list_clusters = {}
            for cluster in oc_list_clusters.get("items", {}):
                cluster_name = cluster.get("metadata", {}).get("name", "")
                cluster_state = cluster.get("status", {}).get("version", {}).get("history", [{}])[0].get("state", None)
                if self.environment["cluster_name_seed"] in cluster_name:
                    current_cluster_count += 1
                    cluster_state = cluster.get("status", {}).get("version", {}).get("history", [{}])[0].get("state", None)
                    if cluster_state == "error":
                        error.append(cluster["name"])
                    elif cluster_state == "Completed":
                        state[cluster_state] = state.get(cluster_state, 0) + 1
                        installed_clusters += 1
#                        required_workers = cluster["nodes"]["compute"]
#                        ready_workers = self.get_workers_ready(self.environment["path"] + "/" + cluster["name"] + "/kubeconfig", cluster["name"])
#                        if ready_workers == required_workers:
#                        clusters_with_all_workers += 1
                    elif cluster_state != "":
                        state[cluster_state] = state.get(cluster_state, 0) + 1
            self.logging.info("Requested Clusters for test %s: %d of %d" % (self.environment["uuid"], current_cluster_count, self.environment["cluster_count"]))
            state_output = ""
            for i in state.items():
                state_output += "(" + str(i[0]) + ": " + str(i[1]) + ") "
                self.logging.info(state_output)
            if error:
                self.logging.warning("Clusters in error state: %s" % error)
            if installed_clusters == self.environment["cluster_count"]:
                # Disable watcher for workers because there is no workers information on the oc get hostedclusters command
                # All clusters ready
                #                if self.environment["wait_for_workers"]:
                #                    if clusters_with_all_workers == self.environment["cluster_count"]:
                #                        self.logging.info("All clusters on Completed status and all clusters with all workers ready. Exiting watcher")
                #                        break
                #                    else:
                #                        self.logging.info(f"Waiting {self.environment['watcher_delay']} seconds for next watcher run")
                #                        time.sleep(self.environment["watcher_delay"])
                #                else:
                self.logging.info("All clusters on Completed status. Exiting watcher")
                break
            else:
                self.logging.info(f"Waiting {self.environment['watcher_delay']} seconds for next watcher run")
                time.sleep(self.environment["watcher_delay"])
        self.logging.debug(self.environment['clusters'])
        self.logging.info("Watcher terminated")

    def get_metadata(self, platform, cluster_name):
        metadata = super().get_metadata(platform, cluster_name)
        self.logging.info(f"Getting information for cluster {cluster_name} from {self.environment['mgmt_cluster_name']}")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        metadata_code, metadata_out, metadata_err = self.utils.subprocess_exec("oc get hostedcluster " + cluster_name + " -n clusters -o json", extra_params={"env": myenv, "universal_newlines": True}, log_output=False)
        try:
            result = json.loads(metadata_out)
        except Exception as err:
            self.logging.error(f"Cannot load metadata for cluster {cluster_name} from {self.environment['mgmt_cluster_name']}")
            self.logging.error(err)
            metadata['status'] = "not found"
            return metadata
        metadata["cluster_name"] = result.get("metadata", {}).get("name", None)
        metadata["cluster_id"] = result.get("spec", {}).get("clusterID", None)
        metadata["network_type"] = result.get("spec", {}).get("networking", {}).get("networkType", None)
        metadata["version"] = result.get("spec", {}).get("release", {}).get("image", None)
        metadata["status"] = result.get("status", {}).get("version", {}).get("history", [{}])[0].get("state", None)
        metadata["zones"] = None
        return metadata

    def get_cluster_id(self, cluster_name):
        self.logging.info(f"Getting clusterID for cluster {cluster_name} from {self.environment['mgmt_cluster_name']}")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        metadata_code, metadata_out, metadata_err = self.utils.subprocess_exec("oc get hostedcluster " + cluster_name + " -n clusters -o json", extra_params={"env": myenv, "universal_newlines": True}, log_output=False)
        try:
            result = json.loads(metadata_out)
        except Exception as err:
            self.logging.error(f"Cannot load metadata for cluster {cluster_name} from {self.environment['mgmt_cluster_name']}")
            self.logging.error(err)
        return result.get("spec", {}).get("clusterID", None)

    def get_mc(self, cluster_id):
        self.logging.debug(f"Get the mgmt cluster of cluster {cluster_id}")
        return self.environment['mgmt_cluster_name']

    def download_kubeconfig(self, cluster_name, path):
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment['mc_kubeconfig']
        self.logging.debug(f"Downloading kubeconfig file for Cluster {cluster_name} from {self.environment['mgmt_cluster_name']} on {path}/kubeconfig")
        starting_time = datetime.datetime.utcnow().timestamp()
        while datetime.datetime.utcnow().timestamp() < starting_time + 5 * 60:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting kubeconfig downloading on {cluster_name} cluster after capturing Ctrl-C")
                return None
            kubeconfig_code, kubeconfig_out, kubeconfig_err = self.utils.subprocess_exec("oc get secret -n clusters " + cluster_name + "-admin-kubeconfig -o json", extra_params={"cwd": path, "env": myenv, "universal_newlines": True})
            if kubeconfig_code == 0:
                try:
                    kubeconfig = base64.b64decode(json.loads(kubeconfig_out).get("data", {}).get("kubeconfig", None)).decode("utf-8")
                except Exception as err:
                    self.logging.error(f"Cannot load kubeconfig for cluster {cluster_name} from {self.environment['mgmt_cluster_name']}. Waiting 5 seconds for the next try...")
                    self.logging.error(err)
                    self.logging.debug(kubeconfig_out)
                    time.sleep(5)
                    continue
                kubeconfig_path = path + "/kubeconfig"
                with open(kubeconfig_path, "w") as kubeconfig_file:
                    kubeconfig_file.write(kubeconfig)
                self.logging.debug(f"Downloaded kubeconfig file for Cluster {cluster_name} and stored at {path}/kubeconfig")
                return kubeconfig_path
            else:
                self.logging.warning(f"Failed to download kubeconfig file for cluster {cluster_name}. Waiting 5 seconds for the next try...")
                time.sleep(5)
        self.logging.error(f"Failed to download kubeconfig file for cluster {cluster_name} after 5 minutes.")
        return None

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment['mc_kubeconfig']
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
        cluster_info['mgmt_cluster_name'] = self.environment['mgmt_cluster_name']
        cluster_info["install_method"] = "hypershiftcli"
        cluster_info['resource_group_name'] = "rg-" + cluster_name
        self.logging.info(f"Deleting cluster {cluster_name} on Azure Hypershiftcli Platform")
        cleanup_code, cleanup_out, cleanup_err = self.utils.subprocess_exec("hypershift destroy cluster azure --name " + cluster_name + " --azure-creds " + self.environment['azure_credentials_file'] + " --resource-group-name " + cluster_info['resource_group_name'], cluster_info["path"] + "/cleanup.log", {"env": myenv, 'preexec_fn': self.utils.disable_signals})
        cluster_delete_end_time = int(datetime.datetime.utcnow().timestamp())
        if cleanup_code == 0:
            cluster_info["status"] = "deleted"
        else:
            cluster_info["status"] = "not deleted"
#        cluster_end_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["destroy_duration"] = cluster_delete_end_time - cluster_start_time
        self.logging.info(f"Checking if Azure Resource Group {cluster_info['resource_group_name']} has been deleted")
        rg_exist_code, rg_exist_out, rg_exist_err = self.utils.subprocess_exec("az group exists --resource-group " + cluster_info['resource_group_name'])
        if rg_exist_out == "true":
            self.logging.warning(f"Hypershift destroy command did not delete the azure resource group {cluster_info['resource_group_name']}")
            self.logging.info(f"Deleting azure resource group {cluster_info['resource_group_name']}...")
            cluster_extra_time = int(datetime.datetime.utcnow().timestamp())
            rg_destroy_code, rg_destroy_out, rg_destroy_err = self.utils.subprocess_exec("az group delete -y --subscription " + self.environment['subscription_id'] + " --resource-group " + cluster_info['resource_group_name'])
            if rg_destroy_code != 0:
                cluster_extra_end = int(datetime.datetime.utcnow().timestamp())
                cluster_info['destroy_all_duration'] = cluster_info['destroy_duration'] + (cluster_extra_end - cluster_extra_time)
            else:
                self.logging.error(f"Failed to manually destroy Azure Resource Group {cluster_info['resource_group_name']}")
        else:
            self.logging.info(f"Azure resource group {cluster_info['resource_group_name']} not found")
            cluster_info['destroy_all_duration'] = cluster_info['destroy_duration']
        try:
            with open(cluster_info['path'] + "/metadata_destroy.json", "w") as metadata_file:
                json.dump(cluster_info, metadata_file)
        except Exception as err:
            self.logging.error(err)
            self.logging.error(f"Failed to write metadata_destroy.json file located at {cluster_info['path']}")
        self.es.index_metadata(cluster_info) if self.es is not None else None

    def wait_for_controlplane_ready(self, cluster_name, wait_time):
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment['mc_kubeconfig']
        starting_time = datetime.datetime.utcnow().timestamp()
        while datetime.datetime.utcnow().timestamp() < starting_time + wait_time * 60:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting install times capturing on {cluster_name} cluster after capturing Ctrl-C")
                return 0
            self.logging.info(f"Getting cluster information for cluster {cluster_name} on {self.environment['mgmt_cluster_name']}")
            cluster_status_code, cluster_status_out, cluster_status_err = self.utils.subprocess_exec("oc get hostedcluster -n clusters " + cluster_name + " -o json", extra_params={"env": myenv, "universal_newlines": True})
            current_time = int(datetime.datetime.utcnow().timestamp())
            try:
                cluster_status = json.loads(cluster_status_out).get("status", {}).get("conditions", [])
            except Exception as err:
                self.logging.error(f"Cannot load command result for cluster {cluster_name}. Waiting 1 seconds for next check...")
                self.logging.error(err)
                time.sleep(1)
                continue
            if any(item["message"] == "The hosted control plane is available" and item["status"] == "True" for item in cluster_status):
                time_to_completed = int(round(current_time - starting_time, 0))
                self.logging.info(f"Control Plane for cluster {cluster_name} is ready after {time_to_completed} seconds")
                return time_to_completed
            else:
                self.logging.info(f"Control Plane for cluster {cluster_name} not ready after {int(round(current_time - starting_time, 0))} seconds, waiting 1 second for the next check")
                time.sleep(1)

    def wait_for_cluster_ready(self, cluster_name, wait_time):
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment['mc_kubeconfig']
        starting_time = datetime.datetime.utcnow().timestamp()
        while datetime.datetime.utcnow().timestamp() < starting_time + wait_time * 60:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting install times capturing on {cluster_name} cluster after capturing Ctrl-C")
                return 0
            self.logging.info(f"Getting cluster information for cluster {cluster_name} on {self.environment['mgmt_cluster_name']}")
            cluster_status_code, cluster_status_out, cluster_status_err = self.utils.subprocess_exec("oc get hostedcluster -n clusters " + cluster_name + " -o json", extra_params={"env": myenv, "universal_newlines": True})
            current_time = int(datetime.datetime.utcnow().timestamp())
            try:
                cluster_status = json.loads(cluster_status_out).get("status", {}).get("version", {}).get("history", [{}])[0].get("state", None)
            except Exception as err:
                self.logging.error(f"Cannot load command result for cluster {cluster_name}. Waiting 5 seconds for next check...")
                self.logging.error(err)
                time.sleep(5)
                continue
            if cluster_status == "Completed":
                time_to_completed = int(round(current_time - starting_time, 0))
                self.logging.info(f"Cluster {cluster_name} status is \"Completed\" after {time_to_completed} seconds")
                return time_to_completed
            else:
                self.logging.info(f"Cluster {cluster_name} status is {cluster_status} after {int(round(current_time - starting_time, 0))} seconds, waiting 15 seconds for the next check")
                time.sleep(15)

    def _wait_for_workers(self, kubeconfig, worker_nodes, wait_time, cluster_name, machinepool_name):
        self.logging.info(f"Waiting {wait_time} minutes for {worker_nodes} workers to be ready on {machinepool_name} machinepool on {cluster_name}")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        result = [machinepool_name]

        starting_time = int(datetime.datetime.utcnow().timestamp())
        self.logging.debug(f"Waiting {wait_time} minutes for nodes to be Ready on cluster {cluster_name} until {datetime.datetime.fromtimestamp(starting_time + wait_time * 60)}")
        while datetime.datetime.utcnow().timestamp() < starting_time + wait_time * 60:
            if self.utils.force_terminate:
                self.logging.error("Exiting workers waiting on the cluster %s after capturing Ctrl-C" % cluster_name)
                return []
            self.logging.info("Getting node information for cluster %s" % cluster_name)
            nodes_code, nodes_out, nodes_err = self.utils.subprocess_exec("oc get nodes -o json", extra_params={"env": myenv, "universal_newlines": True})
            try:
                nodes_json = json.loads(nodes_out)
            except Exception as err:
                self.logging.error(f"Cannot load command result for cluster {cluster_name}. Waiting 15 seconds for next check...")
                self.logging.error(err)
                time.sleep(15)
                continue
            nodes = nodes_json["items"] if "items" in nodes_json else []

            # First we find nodes which label nodePool match the machinepool name and then we check if type:Ready is on the conditions
            ready_nodes = (sum(len(list(filter(lambda x: x.get("type") == "Ready" and x.get("status") == "True", node["status"]["conditions"]))) for node in nodes
                               if node.get("metadata", {}).get("labels", {}).get("hypershift.openshift.io/nodePool") and machinepool_name in node["metadata"]["labels"]["hypershift.openshift.io/nodePool"])
                           if nodes
                           else 0
                           )

            if ready_nodes == worker_nodes:
                self.logging.info(f"Found {ready_nodes}/{worker_nodes} ready nodes on machinepool {machinepool_name} for cluster {cluster_name}. Stopping wait.")
                result.append(ready_nodes)
                result.append(int(datetime.datetime.utcnow().timestamp()) - starting_time)
                return result
            else:
                self.logging.info(f"Found {ready_nodes}/{worker_nodes} ready nodes on machinepool {machinepool_name} for cluster {cluster_name}. Waiting 15 seconds for next check...")
                time.sleep(15)
        self.logging.error(f"Waiting time expired. After {wait_time} minutes there are {ready_nodes}/{worker_nodes} ready nodes on {machinepool_name} machinepool for cluster {cluster_name}")
        result.append(ready_nodes)
        result.append("")
        return result

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment['mc_kubeconfig']
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
        cluster_info["hostedclusters"] = self.environment["cluster_count"]
        cluster_info["install_method"] = "hypershiftcli"
        cluster_info['mgmt_cluster_name'] = self.environment['mgmt_cluster_name']
        self.logging.info(f"Creating cluster {cluster_info['index']} on Azure Hypershift with name {cluster_name} and {cluster_info['workers']} workers")
        cluster_info["path"] = platform.environment["path"] + "/" + cluster_name
        os.mkdir(cluster_info["path"])
        self.logging.debug("Attempting cluster installation")
        self.logging.debug("Output directory set to %s" % cluster_info["path"])

        # Create the resource group
        rg_create_code, rg_create_out, rg_create_err = self.utils.subprocess_exec("az group create --subscription " + self.environment['subscription_id'] + " --location " + self.environment['azure_region'] + " --name rg-" + cluster_name + " --tags TicketId=471", cluster_info["path"] + "/az_group_create.log")
        if rg_create_code != 0:
            self.logging.error(f"Failed to create the azure resource group rg_{cluster_name} for cluster {cluster_name}")
            cluster_info["status"] = "Not Created"
            self.es.index_metadata(cluster_info) if self.es is not None else None
            return 1
        else:
            self.logging.info(f"Azure resource group rg-{cluster_name} created")
            cluster_info['resource_group_name'] = "rg-" + cluster_name
        cluster_cmd = ["hypershift", "create", "cluster", "azure", "--name", cluster_name, " --azure-creds " + self.environment['azure_credentials_file'], "--location", self.environment['azure_region'], "--node-pool-replicas", str(cluster_info["workers"]), "--resource-group-name", "rg-" + cluster_name]
        if platform.environment["wildcard_options"]:
            for param in platform.environment["wildcard_options"].split():
                cluster_cmd.append(param)
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())
        self.logging.info(f"Trying to install cluster {cluster_name} with {cluster_info['workers']} workers up to 5 times")
        trying = 0
        while trying <= 5:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting cluster creation for {cluster_name} after capturing Ctrl-C")
                cluster_info["status"] = "Force terminated"
                self.es.index_metadata(cluster_info) if self.es is not None else None
                return 0
            self.logging.info("Cluster Create Command:")
            self.logging.info(cluster_cmd)
            create_cluster_code, create_cluster_out, create_cluster_err = self.utils.subprocess_exec(" ".join(str(x) for x in cluster_cmd), cluster_info["path"] + "/installation.log", {"env": myenv, 'preexec_fn': self.utils.disable_signals})
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
                    cluster_info["status"] = "Not Created"
                    self.logging.error(f"Cluster {cluster_name} installation failed after 5 retries")
                    self.logging.debug(create_cluster_out)
                    self.es.index_metadata(cluster_info) if self.es is not None else None
                    return 1
            else:
                break
        cluster_info['status'] = "Created"
        self.logging.info(f"Cluster {cluster_name} installation finished on the {trying} try")
        cluster_info["metadata"] = self.get_metadata(platform, cluster_name)
        cluster_info["install_try"] = trying
#        mc_namespace = executor.submit(self._namespace_wait, platform.environment["mc_kubeconfig"], cluster_info["metadata"]["cluster_id"], cluster_name, "Management") if platform.environment["mc_kubeconfig"] != "" else 0
#        cluster_info["mc_namespace_timing"] = mc_namespace.result() - cluster_start_time if platform.environment["mc_kubeconfig"] != "" else None
#        cluster_start_time_on_mc = mc_namespace.result()
        cluster_end_time = int(datetime.datetime.utcnow().timestamp())
        # # Getting againg metadata to update the cluster status
        cluster_info["metadata"] = self.get_metadata(platform, cluster_name)
        cluster_info["install_duration"] = cluster_end_time - cluster_start_time
        self.logging.info(f"Waiting up to 10 minutes until cluster {cluster_name} control plane will be ready on {self.environment['mgmt_cluster_name']}")
        cluster_info["cluster_controlplane_ready_delta"] = self.wait_for_controlplane_ready(cluster_name, 10)
        cluster_info["cluster_controlplane_ready_total"] = sum(x or 0 for x in [cluster_info["install_duration"], cluster_info["cluster_controlplane_ready_delta"]])
        cluster_info["kubeconfig"] = self.download_kubeconfig(cluster_name, cluster_info["path"])
        if not cluster_info["kubeconfig"]:
            self.logging.error(f"Failed to download kubeconfig file for cluster {cluster_name}. Disabling wait for workers and workload execution")
            cluster_info["workers_wait_time"] = None
            cluster_info["status"] = "Completed. Not Access"
            self.es.index_metadata(cluster_info) if self.es is not None else None
            return 1
        if cluster_info["workers_wait_time"]:
            self.logging.info("Starting waiting for worker creation...")
            with concurrent.futures.ThreadPoolExecutor() as wait_executor:
                futures = [wait_executor.submit(self._wait_for_workers, cluster_info["kubeconfig"], cluster_info["workers"], cluster_info["workers_wait_time"], cluster_name, cluster_name)]
                futures.append(wait_executor.submit(self._wait_for_workers, cluster_info["kubeconfig"], platform.environment["extra_machinepool"]["replicas"], cluster_info["workers_wait_time"], cluster_name, platform.environment["extra_machinepool"]["name"])) if "extra_machinepool" in platform.environment else None
                self.logging.info("Waiting for workers finished")
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result[0] == cluster_name:
                        default_pool_workers = int(result[1])
                        if default_pool_workers == cluster_info["workers"]:
                            cluster_info["workers_ready_delta"] = result[2]
                            cluster_info["workers_ready_total"] = sum(x or 0 for x in [cluster_info["cluster_controlplane_ready_total"], cluster_info["workers_ready_delta"]])
                        else:
                            cluster_info['workers_ready'] = None
                            cluster_info['status'] = "Completed, missing workers"
                            self.es.index_metadata(cluster_info) if self.es is not None else None
                            return 1
                    else:
                        extra_pool_workers = int(result[1])
                        if "extra_machinepool" in platform.environment and extra_pool_workers == platform.environment["extra_machinepool"]["replicas"]:
                            # cluster_info["extra_pool_workers_ready"] = result[2] - extra_machine_pool_start_time
                            cluster_info["extra_pool_workers_ready_delta"] = result[2]
                            cluster_info["extra_pool_workers_ready_total"] = sum(x or 0 for x in [cluster_info["cluster_controlplane_ready_total"], cluster_info["extra_poolworkers_ready_delta"]])
                        else:
                            cluster_info["extra_pool_workers_ready"] = None
                            cluster_info['status'] = "Completed, missing extra pool workers"
                            self.es.index_metadata(cluster_info) if self.es is not None else None
                            return 1
        self.logging.info(f"Waiting 60 minutes until cluster {cluster_name} status on {self.environment['mgmt_cluster_name']} will be completed")
        cluster_info["cluster_ready_delta"] = self.wait_for_cluster_ready(cluster_name, 60)
        cluster_info["cluster_ready_total"] = sum(x or 0 for x in [cluster_info["workers_ready_total"], cluster_info["cluster_ready_delta"]])
        cluster_info['status'] = "Completed"
        cluster_info["metadata"]["mgmt_cluster"] = self.get_az_aks_cluster_info(self.environment['mgmt_cluster_name'], self.environment["mc_resource_group"])
        try:
            with open(cluster_info['path'] + "/metadata_install.json", "w") as metadata_file:
                json.dump(cluster_info, metadata_file)
        except Exception as err:
            self.logging.error(err)
            self.logging.error(f"Failed to write metadata_install.json file located at {cluster_info['path']}")
        if self.es is not None:
            self.es.index_metadata(cluster_info)
            self.logging.info("Indexing Management cluster stats")
            os.environ["START_TIME"] = f"{cluster_start_time}"
            os.environ["END_TIME"] = f"{cluster_end_time}"
            self.logging.info("Waiting 2 minutes for HC prometheus to be available for scrapping")
            time.sleep(120)
            self.utils.cluster_load(platform, cluster_name, load="index")

    def _namespace_wait(self, kubeconfig, cluster_id, cluster_name, type):
        start_time = int(datetime.datetime.utcnow().timestamp())
        self.logging.info(f"Capturing namespace creation time on {type} Cluster for {cluster_name}. Waiting 30 minutes until datetime.datetime.fromtimestamp(start_time + 30 * 60)")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        # Waiting 30 minutes for preflight checks to end
        while datetime.datetime.utcnow().timestamp() < start_time + 30 * 60:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting namespace creation waiting for {cluster_name} on the {type} cluster after capturing Ctrl-C")
                return 0
            (oc_project_code, oc_project_out, oc_project_err) = self.utils.subprocess_exec("oc get projects --output json", extra_params={"env": myenv})
            if oc_project_code != 0:
                self.logging.warning(f"Failed to get the project list on the {type} Cluster. Retrying in 5 seconds. Waiting until {datetime.datetime.fromtimestamp(start_time + 30 * 60)}")
                time.sleep(5)
            else:
                try:
                    projects_json = json.loads(oc_project_out)
                except Exception as err:
                    self.logging.warning(oc_project_out)
                    self.logging.warning(oc_project_err)
                    self.logging.warning(err)
                    self.logging.warning(f"Failed to get the project list on the {type} Cluster. Retrying in 5 seconds until {datetime.datetime.fromtimestamp(start_time + 30 * 60)}")
                    time.sleep(5)
                    continue
                namespace_count = 0
                projects = projects_json.get("items", [])
                for project in projects:
                    if cluster_id in project.get("metadata", {}).get("name", ""):
                        namespace_count += 1
                if (type == "Service" and namespace_count == 2) or (type == "Management" and namespace_count == 3):
                    end_time = int(datetime.datetime.utcnow().timestamp())
                    self.logging.info(f"Namespace for {cluster_name} created in {type} Cluster at {datetime.datetime.fromtimestamp(end_time)}")
                    return end_time
                else:
                    self.logging.warning(f"Namespace for {cluster_name} not found in {type} Cluster. Retrying in 5 seconds until {datetime.datetime.fromtimestamp(start_time + 30 * 60)}")
                    time.sleep(5)
        self.logging.error(f"Failed to get namespace for {cluster_name} on the {type} cluster after 15 minutes" % (cluster_name, type))
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
            if cluster_name in nodepool:
                conditions = node.get("status", {}).get("conditions", [])
                for condition in conditions:
                    if "type" in condition and condition["type"] == "Ready":
                        status.append(condition["status"])
        status_list = {i: status.count(i) for i in status}
        ready_nodes = status_list["True"] if "True" in status_list else 0
        return ready_nodes


class HypershiftcliArguments(AzureArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--mc-cluster-name", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_MC_CLUSTER_NAME", default='aro-hcp-aks', help="Azure cluster name of the MC Cluster")
        parser.add_argument("--mc-kubeconfig", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_MC_KUBECONFIG", help="Kubeconfig file for the MC Cluster")
        parser.add_argument("--mc-az-resource-group", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_MC_RESOURCE_GROUP", help="Azure Resource group where MC is installed")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Azure:Hypershiftci")))
            parser.set_defaults(**defaults)

        temp_args, temp_unknown_args = parser.parse_known_args()
        if not temp_args.mc_kubeconfig or not temp_args.mc_az_resource_group:
            parser.error("hcp-burner.py: error: the following arguments (or equivalent definition) are required: --mc-kubeconfig, --mc-az-resource-group")
