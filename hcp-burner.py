#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import time
import importlib
import threading
import signal
from datetime import datetime, timezone
from libs.arguments import Arguments
from libs.logging import Logging
from libs.elasticsearch import Elasticsearch
from libs.utils import Utils

if __name__ == "__main__":
    ts_start = time.time()
    arguments = Arguments(os.environ)
    logging = Logging(arguments["log_level"], arguments["log_file"])
    es = Elasticsearch(logging, arguments["es_url"], arguments["es_index"], arguments["es_insecure"], arguments["es_index_retry"]) if arguments["es_url"] else None
    utils = Utils(logging)

    logging.info(f"Detected {arguments['platform']} as platform")
    try:
        if arguments["subplatform"]:
            logging.info(f"Detected {arguments['subplatform']} as subplatform, loading its module...")
            platform_module_path = "libs.platforms." + arguments["platform"] + "." + arguments["subplatform"] + "." + arguments["subplatform"]
            platform_module = importlib.import_module(platform_module_path)
            PlatformClass = getattr(platform_module, arguments["subplatform"].capitalize())
            platform = PlatformClass(arguments, logging, utils, es)
        else:
            logging.info(f"Subplatform not detected, loading {arguments['platform']} module...")
            platform_module_path = "libs.platforms." + arguments["platform"] + "." + arguments["platform"]
            platform_module = importlib.import_module(platform_module_path)
            PlatformClass = getattr(platform_module, arguments["platform"].capitalize())
            platform = PlatformClass(arguments, logging, utils, es)
    except ImportError as err:
        logging.error("Module not found)")
        logging.error(err)
        sys.exit("Exiting...")
    except AttributeError as err:
        logging.error("Invalid platform class in module")
        logging.error(err)
        sys.exit("Exiting...")

    logging.info(f"Verifying external binaries required by the {arguments['platform']} platform")
    for command in platform.environment["commands"]:
        utils.verify_cmnd(command)

    platform.initialize()

    ts_install_clusters = time.time()
    logging.info(f"Starting install clusters phase")
    if str(platform.environment['install_clusters']).lower() == "true":
        logging.info("Starting capturing Ctrl-C key from this point")
        signal.signal(signal.SIGINT, utils.set_force_terminate)

        watcher = threading.Thread(target=platform.watcher)
        watcher.daemon = True
        watcher.start()

        install_threads = utils.install_scheduler(platform)
        logging.info(f"{len(install_threads)} threads created for installing clusters. Waiting for them to finish")
        for thread in install_threads:
            try:
                thread.join()
            except RuntimeError as err:
                if "cannot join current thread" in err.args[0]:
                    # catchs main thread
                    continue
                else:
                    raise
        watcher.join()
        logging.info(f"Install clusters phase finished in {round(time.time() - ts_install_clusters)} seconds")
    else:
        logging.info("Install clusters phase skipped")

    ts_workloads = time.time()
    logging.info(f"Start workloads phase")
    if 'enabled' in platform.environment['load'] and str(platform.environment['load']['enabled']).lower() == "true":
        platform = utils.get_cluster_info(platform)
        load_threads = utils.load_scheduler(platform)
        logging.info(f"{len(load_threads)} threads created to execute workloads. Waiting for them to finish")
        for thread in load_threads:
            try:
                thread.join()
            except RuntimeError as err:
                if "cannot join current thread" in err.args[0]:
                    # catchs main thread
                    continue
                else:
                    raise
        logging.info(f"Workloads phase finished in {round(time.time() - ts_workloads)} seconds")
    else:
        logging.info("Workloads phase skipped")

    ts_cleanup_clusters = time.time()
    logging.info(f"Starting cleanup clusters phase")
    if str(platform.environment["cleanup_clusters"]).lower() == "true":
        platform = utils.get_cluster_info(platform)
        delete_threads = utils.cleanup_scheduler(platform)
        logging.info(f"{len(delete_threads)} threads created for deleting clusters. Waiting for them to finish")
        for thread in delete_threads:
            try:
                thread.join()
            except RuntimeError as err:
                if "cannot join current thread" in err.args[0]:
                    # catchs main thread
                    continue
                else:
                    raise

        platform.platform_cleanup()
        logging.info(f"Cleanup clusters phase finished in {round(time.time() - ts_cleanup_clusters)} seconds")
    else:
        logging.info("Cleanup clusters phase skipped")
    end_time = time.time()

    # Report phase durations
    logging.info(f"HCP-burner Phases")
    logging.info(f"* Install Phase: {datetime.fromtimestamp(ts_install_clusters, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} to {datetime.fromtimestamp(ts_workloads, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if str(platform.environment['install_clusters']).lower() == "true":
        logging.info(f"  * Install clusters phase duration: {round(ts_workloads - ts_install_clusters)} seconds")
    else:
        logging.info(f"  * Install clusters phase duration: Skipped")
    logging.info(f"* Workloads Phase: {datetime.fromtimestamp(ts_workloads, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} to {datetime.fromtimestamp(ts_cleanup_clusters, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if 'enabled' in platform.environment['load'] and str(platform.environment['load']['enabled']).lower() == "true":
        logging.info(f"  * Workloads phase duration: {round(ts_cleanup_clusters - ts_workloads)} seconds")
    else:
        logging.info(f"  * Workloads phase duration: Skipped")
    logging.info(f"* Cleanup Phase: {datetime.fromtimestamp(ts_cleanup_clusters, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} to {datetime.fromtimestamp(end_time, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if str(platform.environment["cleanup_clusters"]).lower() == "true":
        logging.info(f"  * Cleanup clusters phase duration: {round(end_time - ts_cleanup_clusters)} seconds")
    else:
        logging.info(f"  * Cleanup clusters phase duration: Skipped")
    logging.info(f"* Total duration: {round(end_time - ts_start)} seconds")
    # utils.test_recap(platform)
