#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import datetime
import subprocess
import configparser
import argparse
from packaging import version as ver
from libs.aws import AWS
from libs.platforms.platform import Platform
from libs.platforms.platform import PlatformArguments


class Rosa(Platform):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        aws = AWS(logging, arguments["aws_account_file"], arguments["aws_profile"])
        aws.set_aws_envvars(arguments['aws_profile'], arguments['aws_region'])
        self.environment['aws'] = aws.set_aws_environment(arguments['aws_profile'], arguments['aws_region'])
        self.environment["commands"].append("rosa")
        self.environment["commands"].append("aws")

        self.environment["rosa_env"] = arguments["rosa_env"]

        self.environment["oidc_config_id"] = arguments["oidc_config_id"]
        self.environment["common_operator_roles"] = arguments["common_operator_roles"]

        if arguments["extra_machinepool_name"]:
            self.environment["extra_machinepool"] = {}
            self.environment["extra_machinepool"]["name"] = arguments[
                "extra_machinepool_name"
            ]
            self.environment["extra_machinepool"]["machine_type"] = arguments[
                "extra_machinepool_machine_type"
            ]
            self.environment["extra_machinepool"]["replicas"] = arguments[
                "extra_machinepool_replicas"
            ]
            self.environment["extra_machinepool"]["labels"] = arguments[
                "extra_machinepool_labels"
            ]
            self.environment["extra_machinepool"]["taints"] = arguments[
                "extra_machinepool_taints"
            ]

    def initialize(self):
        super().initialize()

        # ROSA Login
        self.logging.info("Attempting to log in ROSA using `rosa login`")
        rosa_login_command = "rosa login --token " + self.environment["ocm_token"]
        if self.environment["rosa_env"]:
            rosa_login_command += " --env " + self.environment["rosa_env"]
        rosa_code, rosa_out, rosa_err = self.utils.subprocess_exec(rosa_login_command)
        sys.exit("Exiting...") if rosa_code != 0 else self.logging.info(
            "`rosa login` execution OK"
        )

    def _set_oidc_config(self):
        if self.environment["oidc_config_id"]:
            if self._check_oidc_config_id(self.environment["oidc_config_id"]):
                self.environment["oidc_cleanup"] = False
            else:
                self.logging.error(
                    f"OIDC ID {self.environment['oidc_config_id']} not found in rosa list oidc-config"
                )
                return False
        else:
            self.logging.info(
                f"Creating OIDC Provider with prefix {self.environment['cluster_name_seed']}"
            )
            oidc_code, oidc_out, oidc_err = self.utils.subprocess_exec(
                "rosa create oidc-config --mode=auto --managed=false --prefix "
                + self.environment["cluster_name_seed"]
                + " -o json -y",
                extra_params={"universal_newlines": True},
            )
            if oidc_code == 0:
                start_json = oidc_out.find("{")
                self.logging.info(json.loads(oidc_out[start_json:])["id"])
                self.environment["oidc_config_id"] = json.loads(oidc_out[start_json:])[
                    "id"
                ]
                self.environment["oidc_cleanup"] = True
            else:
                self.logging.error(
                    f"Failed to create oidc-config with prefix {self.environment['cluster_name_seed']}"
                )
                self.environment["oidc_cleanup"] = True
                return False
        return True

    def _check_oidc_config_id(self, oidc_config_id):
        self.logging.info(
            f"Verifying if {oidc_config_id} is in a list of OIDC Providers"
        )
        oidc_code, oidc_out, oidc_err = self.utils.subprocess_exec(
            "rosa list oidc-config -o json"
        )
        if oidc_code == 0:
            for oidc_id in json.loads(oidc_out.decode("utf-8")):
                if oidc_id["id"] == oidc_config_id:
                    self.logging.info(f"Found OIDC ID {oidc_config_id}")
                    return True
            self.logging.error(
                f"OIDC ID {oidc_config_id} not found in rosa list oidc-config"
            )
        return False

    def _delete_oidc_config(self):
        self.logging.info(
            f"OIDC Config ID {self.environment['oidc_config_id']} marked to be cleaned up"
        )
        delete_code, delete_out, delete_err = self.utils.subprocess_exec(
            "rosa delete oidc-config --oidc-config-id "
            + self.environment["oidc_config_id"]
            + " -m auto -y",
            self.environment["path"] + "/rosa_delete_oidc-config.log",
        )
        if delete_code != 0:
            self.logging.error(
                f"Unable to delete oidc-config {self.environment['oidc_config_id']}. Please manually delete it using `rosa delete oidc-config --oidc-config-id {self.environment['oidc_config_id']} -m auto -y` and check logfile {self.environment['path']}/rosa_delete_oidc-config.log for errors"
            )
            return False
        else:
            self.logging.info(f"Deleted oidc-config ID {self.environment['oidc_config_id']}")
            return True

    def _create_operator_roles(self):
        self.logging.info("Finding latest installer Role ARN")
        roles_code, roles_out, roles_err = self.utils.subprocess_exec("rosa list account-roles -o json")
        if roles_code == 0:
            self.logging.info("Installer Role ARN list obtained")
            installer_role_version = ver.parse("0")
            installer_role_arn = None
            for role in json.loads(roles_out.decode("utf-8")):
                if role["RoleType"] == "Installer" and role.get("ManagedPolicy", False) and ver.parse(role["Version"]) > installer_role_version:
                    installer_role_arn = role["RoleARN"]
                    installer_role_version = ver.parse(role["Version"])
                    self.logging.info(f"Selected {installer_role_arn} on {installer_role_version} as Installer Role ARN")
            if installer_role_arn is None:
                return False
        else:
            return False
        self.logging.info(f"Creating operator roles for cluster seed {self.environment['cluster_name_seed']} with Installer Role ARN {installer_role_arn}")
        (
            operator_roles_code,
            operator_roles_out,
            operator_roles_err,
        ) = self.utils.subprocess_exec(
            "rosa create operator-roles --prefix "
            + self.environment["cluster_name_seed"]
            + " -m auto -y --hosted-cp --oidc-config-id "
            + self.environment["oidc_config_id"]
            + " --installer-role-arn "
            + installer_role_arn,
            self.environment["path"] + "/rosa_create_operator_roles.log",
        )
        if operator_roles_code == 0:
            self.logging.info(f"Created operator roles for cluster seed {self.environment['cluster_name_seed']}")
            return True
        else:
            return False

    def _delete_operator_roles(self):
        self.logging.info(
            f"Deleting Operator Roles with prefix: {self.environment['cluster_name_seed']}"
        )
        roles_code, roles_out, roles_err = self.utils.subprocess_exec(
            "rosa delete operator-roles --prefix "
            + self.environment["cluster_name_seed"]
            + " -m auto -y",
            self.environment["path"] + "/rosa_delete_operator_roles.log",
        )
        if roles_code != 0:
            self.logging.error(
                f"Unable to delete operator roles. Please manually delete them using `rosa delete operator-roles --prefix {self.environment['cluster_name_seed']} -m auto -y` and check logfile {self.environment['path']}/rosa_delete_operator_roles.log for errors"
            )
            return False
        else:
            self.logging.info(
                f"Deleted operator roles with prefix: {self.environment['cluster_name_seed']}"
            )
            return True

    def platform_cleanup(self):
        super().platform_cleanup()

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)

    def get_workers_ready(self, kubeconfig, cluster_name):
        super().get_workers_ready(kubeconfig, cluster_name)
        return Platform.get_workers_ready(self, kubeconfig, cluster_name)

    def get_metadata(self, cluster_name):
        metadata = {}
        self.logging.info(f"Getting information for cluster {cluster_name}")
        metadata_code, metadata_out, metadata_err = self.utils.subprocess_exec("rosa describe cluster -c " + cluster_name + " -o json", extra_params={"universal_newlines": True})
        try:
            result = json.loads(metadata_out)
        except Exception as err:
            self.logging.error(f"Cannot load metadata for cluster {cluster_name}")
            self.logging.error(err)
        metadata["cluster_name"] = result.get("name", None)
        metadata["cluster_id"] = result.get("id", None)
        metadata["network_type"] = result.get("network", {}).get("type", None)
        metadata["status"] = result.get("state", None)
        metadata["version"] = result.get("version", {}).get("raw_id", None)
        metadata["zones"] = result.get("nodes", {}).get("availability_zones", None)
        return metadata

    def _preflight_wait(self, cluster_id, cluster_name):
        return_data = {}
        start_time = int(datetime.datetime.utcnow().timestamp())
        previous_status = ""
        self.logging.info(f"Collecting preflight times for cluster {cluster_name} during 60 minutes until {datetime.datetime.fromtimestamp(start_time + 60 * 60)}")
        # Waiting 1 hour for preflight checks to end
        while datetime.datetime.utcnow().timestamp() < start_time + 60 * 60:
            if self.utils.force_terminate:
                self.logging.error(f"Exiting preflight times capturing on {cluster_name} cluster after capturing Ctrl-C")
                return 0
            self.logging.info(f"Getting status for cluster {cluster_name}")
            status_code, status_out, status_err = self.utils.subprocess_exec("rosa describe cluster -c " + cluster_id + " -o json", extra_params={"universal_newlines": True})
            current_time = int(datetime.datetime.utcnow().timestamp())
            try:
                current_status = json.loads(status_out)["state"]
            except Exception as err:
                self.logging.error(f"Cannot load metadata for cluster {cluster_name}")
                self.logging.error(err)
                continue
            if current_status != previous_status and previous_status != "":
                return_data[previous_status] = current_time - start_time
                start_time = current_time
                self.logging.info(f"Cluster {cluster_name} moved from {previous_status} status to {current_status} status after {return_data[previous_status]} seconds")
                if current_status == "installing":
                    self.logging.info(f"Cluster {cluster_name} is on installing status. Exiting preflights waiting...")
                    return return_data
            else:
                self.logging.debug(f"Cluster {cluster_name} on {current_status} status. Waiting 2 seconds until {datetime.datetime.fromtimestamp(start_time + 60 * 60)} for next check")
                time.sleep(1)
            previous_status = current_status
        self.logging.error(f"Cluster {cluster_name} on {current_status} status (not installing) after 60 minutes. Exiting preflight waiting...")
        return return_data

    def get_cluster_admin_access(self, cluster_name, path):
        cluster_admin_create_time = int(datetime.datetime.utcnow().timestamp())
        return_data = {}
        self.logging.info(f"Creating cluster-admin user on cluster {cluster_name} (30 minutes timeout)")
        rosa_create_admin_debug_log = open(path + "/" + "rosa_create_admin_debug.log", "w")
        rosa_create_admin_cmd = ["rosa", "create", "admin", "-c", cluster_name, "-o", "json", "--debug"]
        self.logging.debug(rosa_create_admin_cmd)
        # Waiting 30 minutes for cluster-admin user to be created
        while (datetime.datetime.utcnow().timestamp() < cluster_admin_create_time + 30 * 60):
            if self.utils.force_terminate:
                self.logging.error(f"Exiting cluster access process for {cluster_name} cluster after capturing Ctrl-C")
                return return_data
            # Not using subprocess_exec() because this is the only one execution where stdout and stderr goes to different descriptors
            process = subprocess.Popen(rosa_create_admin_cmd, stdout=subprocess.PIPE, stderr=rosa_create_admin_debug_log, cwd=path, universal_newlines=True)
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                self.logging.warning(f"Failed to create cluster-admin user on {cluster_name} with this stdout/stderr:")
                self.logging.warning(stdout)
                self.logging.warning(stderr)
                self.logging.warning(f"Waiting 5 seconds for the next try on {cluster_name} until {datetime.datetime.fromtimestamp(cluster_admin_create_time + 30 * 60)}")
                time.sleep(5)
            else:
                oc_login_time = int(datetime.datetime.utcnow().timestamp())
                self.logging.info(f"cluster-admin user creation succesfull on cluster {cluster_name}")
                return_data["cluster_admin_create"] = (int(datetime.datetime.utcnow().timestamp()) - cluster_admin_create_time)
                self.logging.info(f"Trying to login on cluster {cluster_name} (30 minutes timeout until {datetime.datetime.fromtimestamp(oc_login_time + 30 * 60)}, 5s timeout on oc command)")
                start_json = stdout.find("{")
                while datetime.datetime.utcnow().timestamp() < oc_login_time + 30 * 60:
                    if self.utils.force_terminate:
                        self.logging.error(f"Exiting cluster access process for {cluster_name} cluster after capturing Ctrl-C")
                        return return_data
                    (oc_login_code, oc_login_out, oc_login_err) = self.utils.subprocess_exec(
                        "oc login " + json.loads(stdout[start_json:])["api_url"] + " --username " + json.loads(stdout[start_json:])["username"] + " --password " + json.loads(stdout[start_json:])["password"] + " --kubeconfig " + path + "/kubeconfig --insecure-skip-tls-verify=true --request-timeout=30s",
                        extra_params={"cwd": path, "universal_newlines": True},
                        log_output=False)
                    if oc_login_code != 0:
                        self.logging.debug(f"Waiting 5 seconds until {datetime.datetime.fromtimestamp(oc_login_time + 30 * 60)} for the next try on {cluster_name}")
                        time.sleep(5)
                    else:
                        oc_adm_time_start = int(datetime.datetime.utcnow().timestamp())
                        self.logging.info("Login succesfull on cluster %s" % cluster_name)
                        return_data["cluster_admin_login"] = (int(datetime.datetime.utcnow().timestamp()) - oc_login_time)
                        return_data["kubeconfig"] = path + "/kubeconfig"
                        myenv = os.environ.copy()
                        myenv["KUBECONFIG"] = return_data["kubeconfig"]
                        self.logging.info("Trying to perform oc adm command on cluster %s until %s" % (cluster_name, datetime.datetime.fromtimestamp(oc_adm_time_start + 30 * 60)))
                        while (datetime.datetime.utcnow().timestamp() < oc_adm_time_start + 30 * 60):
                            if self.utils.force_terminate:
                                self.logging.error(f"Exiting cluster access process for {cluster_name} cluster after capturing Ctrl-C")
                                return return_data
                            (oc_adm_code, oc_adm_out, oc_adm_err
                             ) = self.utils.subprocess_exec(
                                "oc adm top images",
                                extra_params={
                                    "cwd": path,
                                    "universal_newlines": True,
                                    "env": myenv,
                                },
                                log_output=False,
                            )
                            if oc_adm_code != 0:
                                self.logging.debug(
                                    "Waiting 5 seconds for the next try on %s"
                                    % cluster_name
                                )
                                time.sleep(5)
                            else:
                                self.logging.info(
                                    "Verified admin access to %s, using %s kubeconfig file."
                                    % (cluster_name, path + "/kubeconfig")
                                )
                                return_data["cluster_oc_adm"] = (
                                    int(datetime.datetime.utcnow().timestamp()) - oc_adm_time_start
                                )
                                return return_data
                        self.logging.error(
                            "Failed to execute `oc adm top images` cluster %s after 30 minutes. Exiting"
                            % cluster_name
                        )
                        return return_data
                self.logging.error(
                    "Failed to login on cluster %s after 30 minutes retries. Exiting"
                    % cluster_name
                )
                return return_data
        self.logging.error(
            "Failed to create cluster-admin user on cluster %s after 30 minutes. Exiting"
            % cluster_name
        )
        return return_data

    def add_machinepool(self, cluster_name, cluster_id, aws_zones, machinepool):
        self.logging.info(
            f"Creating {len(aws_zones)} machinepools {machinepool['name']}-ID on {cluster_name}, one per AWS Zone"
        )
        machines_per_zone = machinepool["replicas"] // len(aws_zones)
        extra_machines = machinepool["replicas"] % len(aws_zones)
        zone_machines = [machines_per_zone] * len(aws_zones)
        if extra_machines > 0:
            zone_machines[-1] += extra_machines
        for id, zone in enumerate(aws_zones):
            if zone_machines[id] == 0:
                continue
            machinepool_cmd = [
                "rosa",
                "create",
                "machinepool",
                "--cluster",
                cluster_id,
                "--instance-type",
                machinepool["machine_type"],
                "--name",
                machinepool["name"] + "-" + str(id),
                "--replicas",
                str(zone_machines[id]),
                "--availability-zone",
                zone,
                "-y",
            ]
            if machinepool["labels"]:
                machinepool_cmd.append("--labels")
                machinepool_cmd.append(machinepool["labels"])
            if machinepool["taints"]:
                machinepool_cmd.append("--taints")
                machinepool_cmd.append(machinepool["taints"])
            (
                machinepool_code,
                machinepool_out,
                machinepool_err,
            ) = self.utils.subprocess_exec(" ".join(str(x) for x in machinepool_cmd))
            if machinepool_code != 0:
                self.logging.error(
                    f"Unable to create machinepool {machinepool['name']}-{str(id)} on {cluster_name}"
                )

    def watcher(self):
        super().watcher()
        self.logging.info(f"Watcher started on {self.environment['platform']}")
        self.logging.info(f"Getting status every {self.environment['watcher_delay']}")
        self.logging.info(f"Expected Clusters: {self.environment['cluster_count']}")
        self.logging.info(f"Manually terminate watcher creating the file {self.environment['path']}/terminate_watcher")
        file_path = os.path.join(self.environment["path"], "terminate_watcher")
        if os.path.exists(file_path):
            os.remove(file_path)
        while not self.utils.force_terminate:
            self.logging.debug(self.environment['clusters'])
            if os.path.isfile(
                os.path.join(self.environment["path"], "terminate_watcher")
            ):
                self.logging.warning("Watcher has been manually set to terminate")
                break

            (
                cluster_list_code,
                cluster_list_out,
                cluster_list_err,
            ) = self.utils.subprocess_exec(
                "rosa list clusters -o json", extra_params={"universal_newlines": True}
            )
            current_cluster_count = 0
            installed_clusters = 0
            clusters_with_all_workers = 0
            state = {}
            error = []
            try:
                rosa_list_clusters = json.loads(cluster_list_out)
            except ValueError as err:
                self.logging.error("Failed to get clusters list: %s" % err)
                self.logging.error(cluster_list_out)
                self.logging.error(cluster_list_err)
                rosa_list_clusters = {}
            for cluster in rosa_list_clusters:
                if (
                    "name" in cluster
                    and self.environment["cluster_name_seed"] in cluster["name"]
                ):
                    current_cluster_count += 1
                    state_key = cluster["state"] if "state" in cluster else ""
                    if state_key == "error":
                        error.append(cluster["name"])
                    elif state_key == "ready":
                        state[state_key] = state.get(state_key, 0) + 1
                        installed_clusters += 1
                        required_workers = cluster["nodes"]["compute"]
                        ready_workers = self.get_workers_ready(self.environment["path"]
                                                               + "/"
                                                               + cluster["name"]
                                                               + "/kubeconfig",
                                                               cluster["name"],
                                                               )
                        if ready_workers == required_workers:
                            clusters_with_all_workers += 1
                    elif state_key != "":
                        state[state_key] = state.get(state_key, 0) + 1
            self.logging.info(
                "Requested Clusters for test %s: %d of %d"
                % (
                    self.environment["uuid"],
                    current_cluster_count,
                    self.environment["cluster_count"],
                )
            )
            state_output = ""
            for i in state.items():
                state_output += "(" + str(i[0]) + ": " + str(i[1]) + ") "
                self.logging.info(state_output)
            if error:
                self.logging.warning("Clusters in error state: %s" % error)
            if installed_clusters == self.environment["cluster_count"]:
                # All clusters ready
                if self.environment["wait_for_workers"]:
                    if clusters_with_all_workers == self.environment["cluster_count"]:
                        self.logging.info(
                            "All clusters on ready status and all clusters with all workers ready. Exiting watcher"
                        )
                        break
                    else:
                        self.logging.info(
                            f"Waiting {self.environment['watcher_delay']} seconds for next watcher run"
                        )
                        time.sleep(self.environment["watcher_delay"])
                else:
                    self.logging.info("All clusters on ready status. Exiting watcher")
                    break
            else:
                self.logging.info(
                    f"Waiting {self.environment['watcher_delay']} seconds for next watcher run"
                )
                time.sleep(self.environment["watcher_delay"])
        self.logging.debug(self.environment['clusters'])
        self.logging.info("Watcher terminated")


class RosaArguments(PlatformArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--rosa-env", action=EnvDefault, env=environment, default='staging', envvar="HCP_BURNER_ROSA_ENV", help="ROSA Environment")
        parser.add_argument("--aws-account-file", action=EnvDefault, env=environment, envvar="HCP_BURNER_AWS_ACCOUNT_FILE", help="File containing the AWS credentials")
        parser.add_argument("--aws-profile", action=EnvDefault, env=environment, envvar="HCP_BURNER_AWS_PROFILE", help="Profile to use if aws file cointains more than one")
        parser.add_argument("--aws-region", action=EnvDefault, env=environment, envvar="HCP_BURNER_AWS_REGION", default='us-east-2', help="Token to access OCM API")
        parser.add_argument("--oidc-config-id", action=EnvDefault, env=environment, envvar="HCP_BURNER_OIDC_CONFIG_ID", help="OIDC Config ID to be used on all the clusters")
        parser.add_argument("--common-operator-roles", action="store_true", help="Create one set of operator roles and use it on all clusters")
        parser.add_argument("--extra-machinepool-name", action=EnvDefault, env=environment, envvar="HCP_BURNER_MACHINE_POOL_NAME", help="Add an extra machinepool with this name after cluster is installed")
        parser.add_argument("--extra-machinepool-machine-type", action=EnvDefault, env=environment, envvar="HCP_BURNER_MACHINE_POOL_MACHINE_TYPE", help="Machine Type of the nodes of the extra machinepool", default="m5.xlarge")
        parser.add_argument("--extra-machinepool-replicas", action=EnvDefault, env=environment, envvar="HCP_BURNER_MACHINE_POOL_REPLICAS", help="Number of replicas of the extra machinepool", type=int, default=3)
        parser.add_argument("--extra-machinepool-labels", action=EnvDefault, env=environment, envvar="HCP_BURNER_MACHINEPOOL_LABELS", type=str, help="Labels to add on the extra machinepool", default=None)
        parser.add_argument("--extra-machinepool-taints", action=EnvDefault, env=environment, envvar="HCP_BURNER_MACHINEPOOL_TAINTS", type=str, help="Taints to add on the extra machinepool", default=None)

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Rosa")))
            parser.set_defaults(**defaults)

        temp_args, temp_unknown_args = parser.parse_known_args()
        if not temp_args.aws_account_file:
            parser.error("hcp-burner.py: error: the following arguments (or equivalent definition) are required: --aws-account-file")

    class EnvDefault(argparse.Action):
        def __init__(self, env, envvar, default=None, **kwargs):
            default = env[envvar] if envvar in env else default
            super(RosaArguments.EnvDefault, self).__init__(
                default=default, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
