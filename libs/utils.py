#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import errno
import string
import signal
import random
import time
import subprocess
import threading
from datetime import datetime, timedelta
from git import Repo


class Utils:
    def __init__(self, logging):
        self.logging = logging
        self.force_terminate = False
        # Counters for tracking execution summary
        self.counters = {
            "clusters_requested": 0,
            "clusters_created_success": 0,
            "clusters_created_failed": 0,
            "workloads_executed_success": 0,
            "workloads_executed_failed": 0,
            "workloads_skipped": 0,
            "clusters_deleted_success": 0,
            "clusters_deleted_failed": 0,
        }
        self._counter_lock = threading.Lock()

    def set_force_terminate(self, signum, frame):
        self.logging.warning("Captured Ctrl-C, sending exit event to watcher, any cluster install/delete will continue its execution")
        self.force_terminate = True

    def increment_counter(self, counter_name, value=1):
        """Thread-safe counter increment"""
        with self._counter_lock:
            if counter_name in self.counters:
                self.counters[counter_name] += value

    def print_execution_summary(self, platform):
        """Print execution summary at the end of the run"""
        self.logging.info("=" * 60)
        self.logging.info("EXECUTION SUMMARY")
        self.logging.info("=" * 60)

        # Installation summary
        requested = self.counters["clusters_requested"]
        created_success = self.counters["clusters_created_success"]
        created_failed = self.counters["clusters_created_failed"]

        self.logging.info("Installation Phase:")
        self.logging.info(f"  * Clusters Requested:          {requested}")
        self.logging.info(f"  * Clusters Created Successfully: {created_success}")
        self.logging.info(f"  * Clusters Failed to Create:     {created_failed}")
        if requested > 0:
            success_rate = (created_success / requested) * 100
            self.logging.info(f"  * Success Rate:                  {success_rate:.1f}%")

        # Workload summary
        workload_success = self.counters["workloads_executed_success"]
        workload_failed = self.counters["workloads_executed_failed"]
        workload_skipped = self.counters["workloads_skipped"]
        workload_total = workload_success + workload_failed + workload_skipped

        self.logging.info("")
        self.logging.info("Workload Phase:")
        self.logging.info(f"  * Workloads Executed Successfully: {workload_success}")
        self.logging.info(f"  * Workloads Failed:                {workload_failed}")
        self.logging.info(f"  * Workloads Skipped:               {workload_skipped}")
        if workload_total > 0:
            success_rate = (workload_success / workload_total) * 100 if (workload_success + workload_failed) > 0 else 0
            self.logging.info(f"  * Success Rate:                    {success_rate:.1f}%")

        # Cleanup summary
        deleted_success = self.counters["clusters_deleted_success"]
        deleted_failed = self.counters["clusters_deleted_failed"]
        deleted_total = deleted_success + deleted_failed

        self.logging.info("")
        self.logging.info("Cleanup Phase:")
        self.logging.info(f"  * Clusters Deleted Successfully: {deleted_success}")
        self.logging.info(f"  * Clusters Failed to Delete:     {deleted_failed}")
        if deleted_total > 0:
            success_rate = (deleted_success / deleted_total) * 100
            self.logging.info(f"  * Success Rate:                  {success_rate:.1f}%")

        # List failed clusters if any
        failed_clusters = []
        for cluster_name, cluster_info in platform.environment.get("clusters", {}).items():
            status = cluster_info.get("status", "unknown")
            if "Failed" in status or status in ("thread_failed", "metadata_not_found", "Delete Failed"):
                failed_clusters.append((cluster_name, status))

        if failed_clusters:
            self.logging.info("")
            self.logging.info("Failed Clusters:")
            for cluster_name, status in failed_clusters:
                self.logging.info(f"  * {cluster_name}: {status}")

        self.logging.info("=" * 60)

    def disable_signals(self):
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    def create_path(self, path):
        try:
            self.logging.info(f"Creating directory {path} if it does not exist")
            os.makedirs(path, exist_ok=True)
        except OSError as err:
            if err.errno != errno.EEXIST:
                self.logging.error(err)
                sys.exit("Exiting...")

    def generate_cluster_name_seed(self, seed):
        cluster_name_seed = seed
        allowed_chars = string.ascii_lowercase + string.digits
        for char in seed:
            if char not in allowed_chars:
                self.logging.error(f"Invalid seed for cluster names: {seed}\n. It must contain only lowercase letters and digits.")
                sys.exit("Exiting...")
        random_string = "".join(random.choice(allowed_chars) for j in range(3))
        if len(seed) > 6:
            self.logging.warning(f"Seed for cluster names is too long ({len(seed)}), truncated to {seed[:6]}")
            cluster_name_seed = seed[:6]
        cluster_name_seed = cluster_name_seed + "-" + random_string
        self.logging.info(f"Selected Cluster Name Seed: {cluster_name_seed}")
        return cluster_name_seed

    def verify_cmnd(self, command):
        help_command = command + " help" if command != "terraform" else command + " -h"
        (cmd_code, cmd_out, cmd_err) = self.subprocess_exec(help_command)
        if cmd_code != 0:
            self.logging.error(cmd_out)
            self.logging.error(cmd_err)
            sys.exit("Exiting...")
        else:
            self.logging.info(f"{command} command validated with -h")

    def subprocess_exec(self, command, output_file=None, extra_params={}, log_output=True):
        """
        Function to execute commands on a shell.
        command: command to execute to be passed to subprocess. For example: "ls -l"
        output_file: if defined, file to store output of the command. It will turn return values to None
        extra_params: if defined, any extra param to be passed to Popen function in a mapping format. For example: extra_params={'cwd': '/tmp', 'universal_newlines': False}

        Function call example: exit_code, out, err = common._subprocess_exec("ls -l", extra_params={'cwd': '/tmp', 'universal_newlines': False})
        """
        self.logging.debug(command)
        stdout = None
        stderr = None
        try:
            log_file = open(output_file, "w") if output_file else subprocess.PIPE
            if isinstance(command, list):
                process = subprocess.Popen(command, stdout=log_file, stderr=log_file, **extra_params)
            else:
                process = subprocess.Popen(command.split(), stdout=log_file, stderr=log_file, **extra_params)
            stdout, stderr = process.communicate()
            if process.returncode != 0 and log_output:
                self.logging.error(f"Failed to execute command: {command}")
                self.logging.error(stdout if stdout else "")
                self.logging.error(stderr if stderr else "")
                if output_file:
                    with open(output_file, "r") as log_read:
                        content = log_read.read()
                        self.logging.error(content)
            return process.returncode, stdout, stderr
        except Exception as err:
            self.logging.error(f"Error executing command: {command}")
            self.logging.error(str(err))
            self.logging.error(stdout if stdout else "")
            self.logging.error(stderr if stderr else "")
            return -1, None, None

    def cleanup_scheduler(self, platform):
        if platform.environment["wait_before_cleanup"] != 0:
            self.logging.info(f"Waiting {platform.environment['wait_before_cleanup']} minutes before starting the cluster deletion")
            time.sleep(platform.environment["wait_before_cleanup"] * 60)
        self.logging.info(f"Attempting to start cleanup process of {len(platform.environment['clusters'])} clusters waiting {platform.environment['delay_between_cleanup']} seconds between each deletion")
        delete_cluster_thread_list = []
        for cluster_name, cluster_info in platform.environment["clusters"].items():
            self.logging.info(f"Attempting to start cleanup process of {cluster_name} on status: {cluster_info['status']}")
            try:
                thread = threading.Thread(
                    target=platform.delete_cluster, args=(platform, cluster_name)
                )
            except Exception as err:
                self.logging.error("Thread creation failed")
                self.logging.error(err)
            delete_cluster_thread_list.append(thread)
            thread.start()
            cluster_info["status"] = "deleting"
            self.logging.debug(
                f"Number of alive threads {threading.active_count()}"
            )
            if platform.environment["delay_between_cleanup"] != 0:
                self.logging.info(
                    f"Waiting {platform.environment['delay_between_cleanup']} seconds before deleting the next cluster"
                )
                time.sleep(platform.environment["delay_between_cleanup"])
        return delete_cluster_thread_list

    # To form the cluster_info dict for cleanup funtions
    # It will be called only when --cleanup-clusters without --install-clusters
    def get_cluster_info(self, platform):
        loop_counter = 0
        while loop_counter < platform.environment["cluster_count"]:
            loop_counter += 1
            cluster_name = platform.environment["cluster_name_seed"] + "-" + str(loop_counter)
            platform.environment["clusters"][cluster_name] = {}
            platform.environment["clusters"][cluster_name]["metadata"] = platform.get_metadata(platform, cluster_name)
            
            # Check if metadata retrieval failed (status not found or metadata_not_found)
            metadata_status = platform.environment["clusters"][cluster_name]["metadata"].get("status")
            if metadata_status is None or metadata_status == "metadata_not_found":
                self.logging.warning(f"[{cluster_name}] Metadata not found after all retries, skipping this cluster")
                platform.environment["clusters"][cluster_name]["status"] = "metadata_not_found"
                continue
            
            platform.environment["clusters"][cluster_name]["status"] = metadata_status
            platform.environment["clusters"][cluster_name]["path"] = platform.environment["path"] + "/" + cluster_name
            platform.environment["clusters"][cluster_name]["kubeconfig"] = platform.environment["clusters"][cluster_name]["path"] + "/kubeconfig"
            platform.environment['clusters'][cluster_name]['workers'] = int(platform.environment["workers"].split(",")[(loop_counter - 1) % len(platform.environment["workers"].split(","))])
        return platform

    def load_scheduler(self, platform):
        load_thread_list = []
        self.logging.info(f"Attempting to start {platform.environment['load']['executor']} {platform.environment['load']['workload']} load process on {len(platform.environment['clusters'])} clusters")
        for cluster_name, cluster_info in platform.environment["clusters"].items():
            self.logging.debug(cluster_info)
            if cluster_info['status'] in ("ready", "installed", "Completed", "Succeeded"):
                self.logging.info(f"Attempting to start load process on {cluster_name}")
                try:
                    thread = threading.Thread(target=self.cluster_load, args=(platform, cluster_name))
                except Exception as err:
                    self.logging.error("Thread creation failed")
                    self.logging.error(err)
                    self.increment_counter("workloads_executed_failed")
                    continue
                load_thread_list.append(thread)
                thread.start()
            else:
                self.logging.warning(f"[{cluster_name}] Skipping workload execution, cluster status: {cluster_info['status']}")
                self.increment_counter("workloads_skipped")
        return load_thread_list

    def install_scheduler(self, platform):
        self.logging.info(
            f"Attempting to start {platform.environment['cluster_count']} clusters with {platform.environment['batch_size']} batch size"
        )
        cluster_thread_list = []
        batch_count = 0
        loop_counter = 0
        try:
            while loop_counter < platform.environment["cluster_count"]:
                self.logging.debug(platform.environment["clusters"])
                if self.force_terminate:
                    loop_counter += 1
                else:
                    create_cluster = False
                    if platform.environment["batch_size"] != 0:
                        if platform.environment["delay_between_batch"] is None:
                            # We add 2 to the batch size. 1 for the main thread and 1 for the watcher
                            while (platform.environment["batch_size"] + 2) <= threading.active_count():
                                # Wait for thread count to drop before creating another
                                time.sleep(1)
                            loop_counter += 1
                            create_cluster = True
                        elif batch_count >= platform.environment["batch_size"]:
                            time.sleep(platform.environment["delay_between_batch"])
                            batch_count = 0
                        else:
                            batch_count += 1
                            loop_counter += 1
                            create_cluster = True
                    else:
                        loop_counter += 1
                        create_cluster = True
                    if create_cluster:
                        self.increment_counter("clusters_requested")
                        if platform.environment["workers"].isdigit():
                            cluster_workers = int(platform.environment["workers"])
                        else:
                            cluster_workers = int(platform.environment["workers"].split(",")[(loop_counter - 1) % len(platform.environment["workers"].split(","))])
                        cluster_name = platform.environment["cluster_name_seed"] + "-" + str(loop_counter)
                        platform.environment["clusters"][cluster_name] = {}
                        try:
                            platform.environment["clusters"][cluster_name]["workers"] = cluster_workers
                            platform.environment["clusters"][cluster_name]["workers_wait_time"] = platform.environment["workers_wait_time"]
                            platform.environment["clusters"][cluster_name]["index"] = loop_counter - 1
                            thread = threading.Thread(target=platform.create_cluster, args=(platform, cluster_name))
                            platform.environment["clusters"][cluster_name]["status"] = "creating"
                        except Exception as err:
                            self.logging.error(f"Failed to create cluster {cluster_name}")
                            self.logging.error(err)
                            platform.environment["clusters"][cluster_name]["status"] = "thread_failed"
                            self.increment_counter("clusters_created_failed")
                        cluster_thread_list.append(thread)
                        thread.start()
                        self.logging.debug("Number of alive threads %d" % threading.active_count())
        except Exception as err:
            self.logging.error(err)
            self.logging.error("Thread creation failed")
        return cluster_thread_list

    def cluster_load(self, platform, cluster_name, load=""):
        load_env = os.environ.copy()
        if 'cluster_start_time_on_mc' in platform.environment['clusters'][cluster_name]:
            load_env["START_TIME"] = f"{platform.environment['clusters'][cluster_name]['cluster_start_time_on_mc']}"
            del platform.environment['clusters'][cluster_name]['cluster_start_time_on_mc']
        if 'cluster_end_time' in platform.environment['clusters'][cluster_name]:
            load_env["END_TIME"] = f"{platform.environment['clusters'][cluster_name]['cluster_end_time']}"
            del platform.environment['clusters'][cluster_name]['cluster_end_time']
        my_path = platform.environment['clusters'][cluster_name]['path']
        load_env["KUBECONFIG"] = platform.environment.get('clusters', {}).get(cluster_name, {}).get('kubeconfig', "")

        # Check AZURE_PROM_TOKEN file age for ARO platform
        # AZURE_PROM_TOKEN is required to scrape metrics from MC (Management Cluster) and it cannot
        # be auto-generated - it requires manual intervention. This check ensures the token file
        # is recent (within 1 hour) to avoid using stale tokens.
        # User can either: 1) Provide a valid token file, or 2) Provide both AZURE_PROM_TOKEN and MC_KUBECONFIG as env vars
        if platform.environment.get("platform") == "aro":
            azure_prom_token_path = platform.environment.get("azure_prom_token_file", "")
            token_from_file = False

            # Option 1: User provided token file
            if azure_prom_token_path and os.path.exists(azure_prom_token_path):
                file_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(azure_prom_token_path))
                should_use_token = False

                if file_age > timedelta(hours=1):
                    self.logging.warning(f"[{cluster_name}] AZURE_PROM_TOKEN file is older than 1 hour (age: {file_age}). File may be stale.")
                    try:
                        response = input(f"[{cluster_name}] Stale AZURE_PROM_TOKEN ({azure_prom_token_path}), manually update the file and confirm (yes/no): ").strip().lower()
                        should_use_token = response in ['yes', 'y']
                    except (EOFError, KeyboardInterrupt):
                        should_use_token = False
                else:
                    should_use_token = True

                if should_use_token:
                    try:
                        with open(azure_prom_token_path, 'r') as token_file:
                            load_env["AZURE_PROM_TOKEN"] = token_file.read().strip()
                            token_from_file = True
                            self.logging.info(f"[{cluster_name}] Successfully loaded AZURE_PROM_TOKEN from file: {azure_prom_token_path}")
                    except Exception as err:
                        self.logging.error(f"[{cluster_name}] Failed to read AZURE_PROM_TOKEN from file {azure_prom_token_path}: {err}")
                        self.increment_counter("workloads_executed_failed")
                        return 1

            # Option 2: User should provide both AZURE_PROM_TOKEN and MC_KUBECONFIG as env vars
            if not token_from_file:
                has_token = "AZURE_PROM_TOKEN" in load_env
                has_mc_kubeconfig = "MC_KUBECONFIG" in load_env

                if not (has_token and has_mc_kubeconfig):
                    # Incomplete configuration: user didn't provide both, remove MC_KUBECONFIG
                    if "MC_KUBECONFIG" in load_env:
                        del load_env["MC_KUBECONFIG"]
                        self.logging.warning(f"[{cluster_name}] Incomplete configuration: Both AZURE_PROM_TOKEN and MC_KUBECONFIG must be provided. Removed MC_KUBECONFIG.")
        else:
            load_env["MC_KUBECONFIG"] = platform.environment.get("mc_kubeconfig", "")

        if not os.path.exists(my_path + '/workload'):
            self.logging.info(f"Cloning workload repo {platform.environment['load']['repo']} on {my_path}/workload")
            try:
                Repo.clone_from(platform.environment['load']['repo'], my_path + '/workload')
            except Exception as err:
                self.logging.error(f"Failed to clone repo {platform.environment['load']['repo']}")
                self.logging.error(err)
                self.increment_counter("workloads_executed_failed")
                return 1
        # Copy executor to the local folder because we saw in the past that we cannot use kube-burner with multiple executions at the same time
        # shutil.copy2(platform.environment['load']['executor'], my_path)
        load_env["ITERATIONS"] = str(platform.environment['clusters'][cluster_name]['workers'] * platform.environment['load']['jobs'])
        if load == "index":
            load_env["EXTRA_FLAGS"] = "--check-health=False"
        else:
            load_env["EXTRA_FLAGS"] = "--churn-duration=" + platform.environment['load']['duration'] + " --churn-percent=10 --churn-delay=30s --timeout=24h"
        # if es_url is not None:
        #     load_env["ES_SERVER"] = es_url
        load_env["LOG_LEVEL"] = "debug"
        load_env["WORKLOAD"] = load if load != "" else platform.environment['load']['workload']
        log_file = load if load != "" else platform.environment['load']['workload']
        load_env["KUBE_DIR"] = my_path
        keys_with_none = [key for key, value in load_env.items() if value is None]
        if keys_with_none:
            self.logging.info(f"Removing environment variables with None value: {', '.join(keys_with_none)}")
        clean_env = {key: value for key, value in load_env.items() if value is not None}
        if not self.force_terminate:
            if load == "index":
                self.logging.info(f"Checking cluster {cluster_name} for available monitoring operator using oc wait...")

                health_cmd = "oc wait --for=condition=Available=True co/monitoring --timeout=60m"

            else:
                self.logging.info(f"Checking cluster {cluster_name} health using oc adm wait-for-stable-cluster...")

                health_cmd = "oc adm wait-for-stable-cluster --minimum-stable-period=15s --timeout=20m"

            health_code, health_out, health_err = self.subprocess_exec(
                health_cmd,
                extra_params={"env": clean_env, "universal_newlines": True}
            )

            if health_code != 0:
                self.logging.error(f"Cluster {cluster_name} is unhealthy or not stable. Skipping workload execution.")
                self.logging.error(health_err)
                self.increment_counter("workloads_executed_failed")
                return 1
            else:
                self.logging.info(f"Cluster {cluster_name} is healthy. Proceeding with workload.")
                if health_out:
                    for line in health_out.strip().splitlines():
                        self.logging.info(f"[{cluster_name}] {line}")
            load_code, load_out, load_err = self.subprocess_exec('./' + platform.environment['load']['script'], my_path + '/' + log_file + '.log', extra_params={'cwd': my_path + "/workload/" + platform.environment['load']['script_path'], 'env': clean_env})
            if load_code != 0:
                self.logging.error(f"Failed to execute workload {platform.environment['load']['script_path'] + '/' + platform.environment['load']['script']} on {cluster_name}")
                self.increment_counter("workloads_executed_failed")
                return 1
            else:
                self.logging.info(f"[{cluster_name}] Workload executed successfully")
                self.increment_counter("workloads_executed_success")
                return 0
        else:
            self.logging.warning(f"Not starting workload on {cluster_name} after capturing Ctrl-C")
            self.increment_counter("workloads_skipped")
            return 0
