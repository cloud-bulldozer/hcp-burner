#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import time
import datetime
import configparser
import argparse
import shlex
from copy import deepcopy
from azure.mgmt.resource.resources.v2022_09_01.models import DeploymentMode, Deployment, DeploymentProperties
from azure.core.exceptions import HttpResponseError
import requests
import subprocess
from kubernetes import client as k8s_client, config as k8s_config
from kubernetes.client.rest import ApiException
from libs.platforms.aro.aro import Aro
from libs.platforms.aro.aro import AroArguments


class Hypershift(Aro):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["customer_rg_name"] = arguments["customer_rg_name"]
        self.environment["ticket_id"] = arguments["ticket_id"]
        self.environment["customer_nsg"] = arguments["customer_nsg"]
        self.environment["customer_vnet_name"] = arguments["customer_vnet_name"]
        self.environment["customer_vnet_subnet1"] = arguments["customer_vnet_subnet1"]
        self.environment["managed_resource_group"] = arguments["managed_resource_group"]
        self.environment["node_size"] = arguments["worker_size"]
        self.environment["infra_size"] = arguments["infra_size"]
        if str(arguments['autoscale']).lower() == "true":
            self.environment['autoscale'] = True
            self.environment["max_replicas"] = arguments["max_replicas"]
            self.environment["min_replicas"] = arguments["min_replicas"]
        else:
            self.environment["autoscale"] = False
            self.environment["max_replicas"] = None
            self.environment["min_replicas"] = None
        self.environment["azure_ad_group_name"] = arguments["azure_ad_group_name"]
        self.environment["add_aro_hcp_infra"] = arguments["add_aro_hcp_infra"]
        self.environment["issuer_url"] = arguments["issuer_url"]
        self.environment["azure_prom_token_file"] = arguments["azure_prom_token_file"]

    def initialize(self):
        super().initialize()
        # Parent class (Aro) already initializes credential and resource_client
        # Set default issuer_url using tenant_id from parent initialize
        tenant_id = self.environment.get("tenant_id")
        if tenant_id and not self.environment.get("issuer_url"):
            self.environment["issuer_url"] = f"https://login.microsoftonline.com/{tenant_id}/v2.0"

        # Convert string boolean arguments to actual booleans
        self.environment["add_aro_hcp_infra"] = self._str_to_bool(self.environment.get("add_aro_hcp_infra", "False"))

        self.logging.info("ARO Hypershift platform initialized")

    def _str_to_bool(self, value):
        """Convert string to boolean. Accepts: true/false, 1/0, yes/no (case insensitive)"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes')
        return bool(value)

    def _get_bicep_template_path(self, template_name):
        """Get the absolute path to a Bicep template file"""
        current_file_dir = os.path.dirname(os.path.abspath(__file__))
        project_base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file_dir))))
        template_path = os.path.join(project_base, f"libs/platforms/aro/bicep/{template_name}")
        if not os.path.exists(template_path):
            template_path = f"libs/platforms/aro/bicep/{template_name}"
        return template_path

    def _create_infrastructure(self, cluster_name, customer_rg_name, location, ticket_id, customer_nsg, customer_vnet_name, customer_vnet_subnet1, cluster_path):
        """
        Create resource group and infrastructure deployment.

        Args:
            cluster_name: Name of the cluster
            customer_rg_name: Name of the resource group
            location: Azure region/location
            ticket_id: Ticket ID for resource tags
            customer_nsg: Network Security Group name
            customer_vnet_name: Virtual Network name
            customer_vnet_subnet1: Virtual Network Subnet 1 name
            cluster_path: Path to cluster directory for compiled templates

        Returns:
            tuple: (key_vault_name, customer_rg_name) on success

        Raises:
            Exception: If resource group creation or infrastructure deployment fails
        """
        # Step 1: Create Resource Group
        self.logging.info(f"[{cluster_name}] Creating resource group {customer_rg_name}")
        from azure.mgmt.resource.resources.v2022_09_01.models import ResourceGroup
        resource_group_params = ResourceGroup(location=location, tags={"TicketId": ticket_id})

        try:
            self.resource_client.resource_groups.create_or_update(
                resource_group_name=customer_rg_name,
                parameters=resource_group_params
            )
            self.logging.info(f"[{cluster_name}] Resource group {customer_rg_name} created successfully")
        except HttpResponseError as err:
            self.logging.error(f"[{cluster_name}] Failed to create resource group {customer_rg_name}: {err}")
            raise Exception(f"Resource group creation failed: {err}")

        # Step 2: Create Infrastructure Deployment
        self.logging.info(f"[{cluster_name}] Creating infrastructure deployment")
        bicep_template_path = self._get_bicep_template_path("customer-infra.bicep")

        # Prepare parameters for infrastructure deployment
        infra_parameters = {
            "customerNsgName": {"value": customer_nsg},
            "customerVnetName": {"value": customer_vnet_name},
            "customerVnetSubnetName": {"value": customer_vnet_subnet1}
        }

        # Compile Bicep template to JSON
        import subprocess
        output_file = os.path.join(cluster_path, "customer-infra.json")
        compile_cmd = f"az bicep build --file {shlex.quote(bicep_template_path)} --outfile {shlex.quote(output_file)}"
        compile_result = subprocess.run(compile_cmd, shell=True, capture_output=True, text=True)

        if compile_result.returncode != 0:
            self.logging.error(f"[{cluster_name}] Failed to compile Bicep template: {compile_result.stderr}")
            raise Exception(f"Bicep compilation failed: {compile_result.stderr}")

        compiled_template_path = f"{cluster_path}/customer-infra.json"
        with open(compiled_template_path, 'r') as f:
            template_json = json.load(f)

        # Create deployment
        deployment_properties = DeploymentProperties(
            mode=DeploymentMode.INCREMENTAL,
            template=template_json,
            parameters=infra_parameters
        )
        deployment = Deployment(properties=deployment_properties)

        try:
            deployment_operation = self.resource_client.deployments.begin_create_or_update(
                resource_group_name=customer_rg_name,
                deployment_name="infra",
                parameters=deployment
            )
            deployment_result = deployment_operation.result()
            self.logging.info(f"[{cluster_name}] Infrastructure deployment created successfully")

            # Save deployment result JSON to file
            deployment_output_file = os.path.join(cluster_path, "infra-deployment-result.json")
            try:
                with open(deployment_output_file, 'w') as f:
                    json.dump(deployment_result.as_dict(), f, indent=2, default=str)
                self.logging.info(f"[{cluster_name}] Infrastructure deployment result saved to {deployment_output_file}")
            except Exception as save_err:
                self.logging.warning(f"[{cluster_name}] Failed to save deployment result JSON: {save_err}")
        except HttpResponseError as err:
            self.logging.error(f"[{cluster_name}] Failed to create infrastructure deployment: {err}")
            raise Exception(f"Infrastructure deployment failed: {err}")

        # Step 3: Get Key Vault Name from Infrastructure Deployment
        self.logging.info(f"[{cluster_name}] Retrieving Key Vault name from infrastructure deployment")
        try:
            infra_deployment = self.resource_client.deployments.get(
                resource_group_name=customer_rg_name,
                deployment_name="infra"
            )
            outputs = infra_deployment.properties.outputs
            if outputs and "keyVaultName" in outputs:
                key_vault_name = outputs["keyVaultName"]["value"]
            else:
                self.logging.error(f"[{cluster_name}] Key Vault name not found in deployment outputs")
                raise Exception("Key Vault name not found in deployment outputs")
            self.logging.info(f"[{cluster_name}] Key Vault name: {key_vault_name}")
        except HttpResponseError as err:
            self.logging.error(f"[{cluster_name}] Failed to retrieve Key Vault name: {err}")
            raise Exception(f"Key Vault query failed: {err}")

        return key_vault_name, customer_rg_name

    def _check_cluster_exists_and_ready(self, cluster_name, customer_rg_name, subscription_id):
        """
        Check if the cluster already exists and is in a succeeded/ready state.

        Returns:
            tuple: (exists: bool, is_ready: bool, cluster_info: dict or None)
        """
        try:
            api_version = "2024-06-10-preview"
            cluster_resource_id = f"/subscriptions/{subscription_id}/resourceGroups/{customer_rg_name}/providers/Microsoft.RedHatOpenShift/hcpOpenShiftClusters/{cluster_name}"
            cluster_url = f"https://management.azure.com{cluster_resource_id}?api-version={api_version}"

            # Get access token
            token = self.credential.get_token("https://management.azure.com/.default").token

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            response = requests.get(cluster_url, headers=headers)

            if response.status_code == 404:
                self.logging.info(f"[{cluster_name}] Cluster does not exist")
                return False, False, None
            elif response.status_code == 200:
                cluster_data = response.json()
                provisioning_state = cluster_data.get("properties", {}).get("provisioningState", "")

                self.logging.info(f"[{cluster_name}] Cluster exists with provisioning state: {provisioning_state}")

                # Check if cluster is in a succeeded/ready state
                is_ready = provisioning_state.lower() == "succeeded"

                if is_ready:
                    self.logging.info(f"[{cluster_name}] Cluster is already in succeeded state, skipping creation")
                else:
                    self.logging.info(f"[{cluster_name}] Cluster exists but is in state '{provisioning_state}', will recreate or update")

                return True, is_ready, cluster_data
            else:
                self.logging.warning(f"[{cluster_name}] Unexpected status code {response.status_code} when checking cluster")
                return False, False, None

        except requests.exceptions.RequestException as err:
            self.logging.warning(f"[{cluster_name}] Error checking if cluster exists: {err}")
            return False, False, None
        except Exception as err:
            self.logging.warning(f"[{cluster_name}] Unexpected error checking cluster existence: {err}")
            return False, False, None

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cluster_info["install_method"] = "az"
        cluster_info["hostedclusters"] = self.environment["cluster_count"]
        cluster_info["environment"] = self.environment["aro_env"]
        cluster_info["autoscale"] = self.environment["autoscale"]
        cluster_info["max_replicas"] = self.environment["max_replicas"]
        cluster_info["min_replicas"] = self.environment["min_replicas"]

        # Get parameters from environment or use defaults
        customer_rg_name = self.environment.get("customer_rg_name") or f"{cluster_name}-rg"
        subscription = self.environment.get("subscription_id")
        location = self.environment.get("azure_region", "eastus")
        ticket_id = self.environment.get("ticket_id", "default")
        customer_nsg = self.environment.get("customer_nsg") or f"{cluster_name}-nsg"
        customer_vnet_name = self.environment.get("customer_vnet_name") or f"{cluster_name}-vnet"
        customer_vnet_subnet1 = self.environment.get("customer_vnet_subnet1") or f"{cluster_name}-subnet1"
        managed_resource_group = self.environment.get("managed_resource_group") or f"{cluster_name}-managed-rg"

        cluster_info["path"] = platform.environment["path"] + "/" + cluster_name
        os.makedirs(cluster_info["path"], exist_ok=True)

        # Check if cluster already exists and is ready
        self.logging.info(f"[{cluster_name}] Checking if cluster already exists in resource group {customer_rg_name}")
        cluster_exists, cluster_ready, existing_cluster_data = self._check_cluster_exists_and_ready(
            cluster_name, customer_rg_name, subscription
        )

        if cluster_exists and cluster_ready:
            self.logging.info(f"[{cluster_name}] Cluster already exists and is ready, skipping creation")
            cluster_info["status"] = "ready"

            # Try to get existing cluster information
            try:
                # Get metadata from existing cluster
                cluster_info["metadata"] = self.get_metadata(platform, cluster_name)

                # Try to download kubeconfig if not already present
                if not cluster_info.get("kubeconfig"):
                    try:
                        # Get issuer_url from environment (default already set in initialize if needed)
                        issuer_url = self.environment.get("issuer_url")
                        kubeconfig_path = self.download_kubeconfig(
                            cluster_name=cluster_name,
                            platform=platform,
                            issuer_url=issuer_url,
                            customer_rg_name=customer_rg_name
                        )
                        cluster_info["kubeconfig"] = kubeconfig_path
                        self.logging.info(f"[{cluster_name}] Kubeconfig downloaded for existing cluster")
                    except Exception as kube_err:
                        self.logging.warning(f"[{cluster_name}] Failed to download kubeconfig for existing cluster: {kube_err}")
                        cluster_info["kubeconfig"] = None

                # Set resource group and other info
                cluster_info["resource_group"] = customer_rg_name
                cluster_info["install_duration"] = 0  # Already exists, no install time
                cluster_info["cluster_ready_time"] = 0

                # Try to get key vault name from existing infrastructure deployment
                try:
                    infra_deployment = self.resource_client.deployments.get(
                        resource_group_name=customer_rg_name,
                        deployment_name="infra"
                    )
                    if infra_deployment.properties and infra_deployment.properties.outputs:
                        outputs = infra_deployment.properties.outputs
                        if outputs and "keyVaultName" in outputs:
                            cluster_info["key_vault_name"] = outputs["keyVaultName"]["value"]
                except Exception:
                    pass  # Key vault name not critical for existing clusters

                # Ensure directory exists and save metadata
                try:
                    os.makedirs(cluster_info['path'], exist_ok=True)
                    metadata_install_file = os.path.join(cluster_info['path'], "metadata_install.json")
                    with open(metadata_install_file, "w") as metadata_file:
                        json.dump(cluster_info, metadata_file, indent=2)
                    self.logging.info(f"[{cluster_name}] Metadata install file written to {metadata_install_file}")
                except Exception as err:
                    self.logging.warning(f"[{cluster_name}] Failed to write metadata_install.json: {err}")
                return 0
            except Exception as err:
                self.logging.warning(f"[{cluster_name}] Error processing existing cluster information: {err}")
                self.logging.info(f"[{cluster_name}] Proceeding with cluster creation despite error")

        self.logging.info(f"[{cluster_name}] Creating ARO HCP cluster in resource group {customer_rg_name}")
        cluster_start_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        cluster_info["status"] = "Creating"

        try:
            # Create infrastructure (resource group and infrastructure deployment)
            try:
                key_vault_name, customer_rg_name = self._create_infrastructure(
                    cluster_name=cluster_name,
                    customer_rg_name=customer_rg_name,
                    location=location,
                    ticket_id=ticket_id,
                    customer_nsg=customer_nsg,
                    customer_vnet_name=customer_vnet_name,
                    customer_vnet_subnet1=customer_vnet_subnet1,
                    cluster_path=cluster_info["path"]
                )
            except Exception as err:
                error_msg = str(err)
                if "Resource group creation failed" in error_msg:
                    cluster_info["status"] = "Failed - Resource Group Creation"
                elif "Bicep compilation failed" in error_msg:
                    cluster_info["status"] = "Failed - Bicep Compilation"
                elif "Infrastructure deployment failed" in error_msg:
                    cluster_info["status"] = "Failed - Infrastructure Deployment"
                elif "Key Vault" in error_msg:
                    cluster_info["status"] = "Failed - Key Vault Query"
                else:
                    cluster_info["status"] = "Failed - Infrastructure Setup"
                return 1

            # Step 4: Create ARO HCP Cluster Deployment
            self.logging.info(f"[{cluster_name}] Step 4: Creating ARO HCP cluster deployment")
            cluster_bicep_path = self._get_bicep_template_path("cluster.bicep")

            # Compile cluster Bicep template
            output_file = os.path.join(cluster_info['path'], "cluster.json")
            compile_cmd = f"az bicep build --file {shlex.quote(cluster_bicep_path)} --outfile {shlex.quote(output_file)}"
            compile_result = subprocess.run(compile_cmd, shell=True, capture_output=True, text=True)

            if compile_result.returncode != 0:
                self.logging.error(f"[{cluster_name}] Failed to compile cluster Bicep template: {compile_result.stderr}")
                cluster_info["status"] = "Failed - Cluster Bicep Compilation"
                return 1

            compiled_cluster_template_path = f"{cluster_info['path']}/cluster.json"
            with open(compiled_cluster_template_path, 'r') as f:
                cluster_template_json = json.load(f)

            # Prepare parameters for cluster deployment
            cluster_parameters = {
                "vnetName": {"value": customer_vnet_name},
                "subnetName": {"value": customer_vnet_subnet1},
                "nsgName": {"value": customer_nsg},
                "clusterName": {"value": cluster_name},
                "managedResourceGroupName": {"value": managed_resource_group},
                "keyVaultName": {"value": key_vault_name}
            }

            cluster_start_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            # Create cluster deployment
            cluster_deployment_properties = DeploymentProperties(
                mode=DeploymentMode.INCREMENTAL,
                template=cluster_template_json,
                parameters=cluster_parameters
            )
            cluster_deployment = Deployment(properties=cluster_deployment_properties)

            try:
                cluster_deployment_operation = self.resource_client.deployments.begin_create_or_update(
                    resource_group_name=customer_rg_name,
                    deployment_name="aro-hcp",
                    parameters=cluster_deployment
                )
                cluster_info["status"] = "Installing"

                # Wait for deployment to complete
                self.logging.info(f"[{cluster_name}] Waiting for cluster deployment to complete")
                cluster_deployment_result = cluster_deployment_operation.result()
                self.logging.info(f"[{cluster_name}] ARO HCP cluster deployment created successfully")

                # Save cluster deployment result JSON to file
                cluster_deployment_output_file = os.path.join(cluster_info['path'], "cluster-deployment-result.json")
                try:
                    with open(cluster_deployment_output_file, 'w') as f:
                        json.dump(cluster_deployment_result.as_dict(), f, indent=2, default=str)
                    self.logging.info(f"[{cluster_name}] Cluster deployment result saved to {cluster_deployment_output_file}")
                except Exception as save_err:
                    self.logging.warning(f"[{cluster_name}] Failed to save cluster deployment result JSON: {save_err}")

                # Wait for cluster provisioning state to be Succeeded or Failed (up to 30 minutes)
                self.logging.info(f"[{cluster_name}] Waiting for cluster provisioning state to be Succeeded or Failed (max 30 minutes)")
                provisioning_start_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                wait_timeout = 30 * 60  # 30 minutes in seconds
                check_interval = 30  # Check every 30 seconds
                provisioning_state = None
                cluster_ready_time = None

                while datetime.datetime.now(datetime.timezone.utc).timestamp() < provisioning_start_time + wait_timeout:
                    if self.utils.force_terminate:
                        self.logging.error(f"[{cluster_name}] Exiting cluster creation after capturing Ctrl-C")
                        return 0

                    try:
                        # Get current deployment status
                        deployment = self.resource_client.deployments.get(
                            resource_group_name=customer_rg_name,
                            deployment_name="aro-hcp"
                        )

                        if deployment.properties and deployment.properties.provisioning_state:
                            provisioning_state = deployment.properties.provisioning_state
                            self.logging.info(f"[{cluster_name}] Cluster deployment provisioning state: {provisioning_state}")

                            if provisioning_state == "Succeeded":
                                cluster_info["status"] = "ready"
                                cluster_ready_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                                self.logging.info(f"[{cluster_name}] Cluster deployment provisioning state is Succeeded, status updated to ready")
                                break
                            elif provisioning_state == "Failed":
                                cluster_info["status"] = "Failed - Cluster Deployment"
                                self.logging.error(f"[{cluster_name}] Cluster deployment provisioning state is Failed")
                                return 1
                            else:
                                cluster_info["status"] = "Installing"
                                elapsed_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - provisioning_start_time
                                self.logging.info(f"[{cluster_name}] Cluster deployment provisioning state is Running (elapsed: {elapsed_time}s), waiting...")
                        else:
                            self.logging.warning(f"[{cluster_name}] Could not determine provisioning state from deployment, waiting...")
                            cluster_info["status"] = "Installing"
                    except HttpResponseError as err:
                        self.logging.warning(f"[{cluster_name}] Error checking deployment status: {err}, waiting...")

                    # Wait before next check
                    time.sleep(check_interval)

                # Check if we timed out
                if provisioning_state not in ["Succeeded", "Failed"]:
                    elapsed_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - provisioning_start_time
                    self.logging.error(f"[{cluster_name}] Did not reach Succeeded or Failed state within 30 minutes (elapsed: {elapsed_time}s, final state: {provisioning_state})")
                    cluster_info["status"] = "Failed - Timeout"
                    return 1

            except HttpResponseError as err:
                self.logging.error(f"[{cluster_name}] Failed to create ARO HCP cluster deployment: {err}")
                cluster_info["status"] = "Failed - Cluster Deployment"
                return 1

        except Exception as err:
            self.logging.error(f"[{cluster_name}] Unexpected error during cluster creation: {err}")
            cluster_info["status"] = "Failed - Unexpected Error"
            return 1

        # Get metadata
        cluster_info["metadata"] = self.get_metadata(platform, cluster_name)

        # Set mgmt_cluster_name from MC_NAME environment variable if available
        mc_name = os.environ.get("MC_NAME")
        if mc_name:
            cluster_info["metadata"]["mgmt_cluster"] = {}
            cluster_info["metadata"]["mgmt_cluster"]["cluster_name"] = mc_name
            cluster_info["mgmt_cluster_name"] = mc_name
            self.logging.info(f"[{cluster_name}] Set mgmt_cluster_name from MC_NAME environment variable: {mc_name}")
        else:
            self.logging.debug(f"[{cluster_name}] MC_NAME environment variable not set, mgmt_cluster_name will not be set")

        # Create nodepools before downloading kubeconfig
        if cluster_info.get("workers", 0) > 0:
            self.logging.info(f"[{cluster_name}] Creating nodepools with {cluster_info.get('workers')} workers")
            try:
                # Get nodepool parameters from cluster_info or use defaults
                replica = cluster_info.get("workers", 0)
                node_size = cluster_info.get("node_size") or self.environment.get("node_size")
                autoscale = cluster_info.get("autoscale", False)
                max_replica = cluster_info.get("max_replicas") if autoscale else None
                min_replica = cluster_info.get("min_replicas") if autoscale else None
                # Default to False if not specified
                add_aro_hcp_infra = self.environment.get("add_aro_hcp_infra")
                if add_aro_hcp_infra is None:
                    add_aro_hcp_infra = False

                self.create_nodepool(
                    cluster_name=cluster_name,
                    replica=replica,
                    max_replica=max_replica,
                    min_replica=min_replica,
                    node_size=node_size,
                    autoscale=autoscale,
                    customer_rg_name=customer_rg_name,
                    add_aro_hcp_infra=add_aro_hcp_infra
                )
                self.logging.info(f"[{cluster_name}] Nodepools created successfully")
            except Exception as err:
                self.logging.error(f"[{cluster_name}] Failed to create nodepools: {err}")
                self.logging.warning(f"[{cluster_name}] Continuing with kubeconfig download despite nodepool creation failure")
        else:
            self.logging.info(f"[{cluster_name}] No workers specified, skipping nodepool creation")

        # Download kubeconfig
        self.logging.info(f"[{cluster_name}] Downloading kubeconfig")
        kubeconfig_start_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        try:
            # Get issuer_url from environment (default already set in initialize if needed)
            issuer_url = self.environment.get("issuer_url")
            kubeconfig_path = self.download_kubeconfig(
                cluster_name=cluster_name,
                platform=platform,
                issuer_url=issuer_url,
                customer_rg_name=customer_rg_name
            )
            kubeconfig_end_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            cluster_info["kubeconfig"] = kubeconfig_path
            cluster_info["kubeconfig_download_time"] = kubeconfig_end_time - kubeconfig_start_time
            self.logging.info(f"[{cluster_name}] Kubeconfig downloaded successfully in {cluster_info['kubeconfig_download_time']} seconds")
        except Exception as err:
            self.logging.error(f"[{cluster_name}] Failed to download kubeconfig file: {err}")
            self.logging.error(f"[{cluster_name}] Disabling wait for workers and workload execution")
            cluster_info["kubeconfig"] = None
            cluster_info["workers_wait_time"] = None
            cluster_info["status"] = "Ready. Not Access"
            return 1

        # Set cluster_end_time before waiting for workers (install_duration should not include worker ready time)
        cluster_end_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        cluster_info["status"] = "installed"
        cluster_info['cluster_end_time'] = cluster_end_time
        if cluster_ready_time:
            cluster_info['cluster_start_time_on_mc'] = cluster_start_time
            cluster_info["cluster_ready_time"] = cluster_ready_time - cluster_start_time
            cluster_info["install_duration"] = cluster_info["cluster_ready_time"]
        else:
            cluster_info["cluster_ready_time"] = None
        cluster_info["resource_group"] = customer_rg_name
        cluster_info["key_vault_name"] = key_vault_name
        cluster_info["managed_resource_group"] = managed_resource_group

        # Wait for workers if configured
        if cluster_info["workers_wait_time"]:
            self.logging.info(f"[{cluster_name}] Waiting for workers to be ready")
            try:
                # Get expected worker count from cluster_info
                expected_workers = cluster_info.get("workers", 0)
                autoscale = cluster_info.get("autoscale", False)
                if expected_workers > 0:
                    # Wait for default worker nodepool (usually "np-static" or "workers")
                    result = self._wait_for_workers(
                        kubeconfig=cluster_info["kubeconfig"],
                        worker_nodes=expected_workers,
                        wait_time=cluster_info["workers_wait_time"],
                        cluster_name=cluster_name,
                        machinepool_name="np-scale" if autoscale else "np-static"
                    )

                    if result and len(result) >= 3:
                        ready_workers = int(result[1]) if result[1] else 0
                        ready_timestamp = result[2] if result[2] else None

                        if ready_workers == expected_workers and ready_timestamp:
                            cluster_info["workers_ready"] = ready_timestamp - cluster_start_time
                            self.logging.info(f"[{cluster_name}] All {ready_workers} workers are ready")
                        else:
                            cluster_info["workers_ready"] = None
                            cluster_info["status"] = "Ready, missing workers"
                            self.logging.warning(f"[{cluster_name}] Only {ready_workers}/{expected_workers} workers are ready")
                    else:
                        cluster_info["workers_ready"] = None
                        self.logging.warning(f"[{cluster_name}] Failed to get workers ready status")
                else:
                    self.logging.info(f"[{cluster_name}] No workers specified, skipping worker wait")
            except Exception as err:
                self.logging.error(f"[{cluster_name}] Error waiting for workers: {err}")
                cluster_info["workers_ready"] = None

        # Handle infra node setup if infra nodepool was created
        add_aro_hcp_infra = self.environment.get("add_aro_hcp_infra", False)
        if add_aro_hcp_infra and cluster_info.get("kubeconfig"):
            self.logging.info(f"[{cluster_name}] Infra nodepool was requested, waiting for infra nodes and configuring components")
            try:
                # Wait for infra nodes to be ready (default 2 nodes, 15 min timeout)
                infra_nodes_ready = self._wait_for_infra_nodes(
                    kubeconfig=cluster_info["kubeconfig"],
                    cluster_name=cluster_name,
                    expected_infra_nodes=2,
                    wait_time=15
                )
                cluster_info["infra_nodes_ready"] = infra_nodes_ready

                if infra_nodes_ready >= 2:
                    # Move infrastructure components to infra nodes
                    infra_move_start = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                    move_success = self._move_infra_components(
                        kubeconfig=cluster_info["kubeconfig"],
                        cluster_name=cluster_name
                    )
                    infra_move_end = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                    cluster_info["infra_components_moved"] = move_success
                    cluster_info["infra_setup_duration"] = infra_move_end - infra_move_start
                    if move_success:
                        self.logging.info(f"[{cluster_name}] Infrastructure components configured to use infra nodes")
                    else:
                        self.logging.warning(f"[{cluster_name}] Some infrastructure components may not be configured correctly")
                else:
                    self.logging.warning(f"[{cluster_name}] Not enough infra nodes ready, skipping component migration")
                    cluster_info["infra_components_moved"] = False
            except Exception as err:
                self.logging.error(f"[{cluster_name}] Error setting up infra components: {err}")
                cluster_info["infra_components_moved"] = False

        self.logging.info(f"[{cluster_name}] ARO HCP cluster installation completed successfully")
        self.logging.info(f"[{cluster_name}] Total installation duration: {cluster_info['install_duration']} seconds")
        if cluster_info.get("cluster_ready_time"):
            self.logging.info(f"[{cluster_name}] Cluster ready time: {cluster_info['cluster_ready_time']} seconds")
        if cluster_info.get("workers_ready"):
            self.logging.info(f"[{cluster_name}] Workers ready time: {cluster_info['workers_ready']} seconds")

        # Ensure directory exists and store metadata
        try:
            os.makedirs(cluster_info['path'], exist_ok=True)
            metadata_install_file = os.path.join(cluster_info['path'], "metadata_install.json")
            with open(metadata_install_file, "w") as metadata_file:
                json.dump(cluster_info, metadata_file, indent=2)
            self.logging.info(f"[{cluster_name}] Metadata install file written to {metadata_install_file}")
        except Exception as err:
            self.logging.error(f"[{cluster_name}] Failed to write metadata_install.json: {err}")
            self.logging.error(f"[{cluster_name}] Attempted path: {cluster_info.get('path', 'N/A')}")

        # Index to ES if available
        if self.es is not None:
            self.logging.info(f"[{cluster_name}] ES is available, indexing cluster metadata")
            try:
                cluster_info_copy = deepcopy(cluster_info)
                del cluster_info_copy['cluster_start_time_on_mc']
                del cluster_info_copy['cluster_end_time']
                self.es.index_metadata(cluster_info_copy)
                self.logging.info(f"[{cluster_name}] Successfully indexed cluster metadata to ES")
            except Exception as err:
                self.logging.error(f"[{cluster_name}] Failed to index metadata to ES: {err}")
            self.logging.info(f"[{cluster_name}] Indexing Management cluster stats")
            try:
                self.utils.cluster_load(platform, cluster_name, load="index")
            except Exception as err:
                self.logging.error(f"[{cluster_name}] Failed to execute cluster_load (index): {err}")
        else:
            self.logging.warning(f"[{cluster_name}] ES is not available (self.es is None), skipping ES indexing. Check if HCP_BURNER_ES_URL is set.")
        return 0

    def delete_cluster(self, platform, cluster_name):
        super().delete_cluster(platform, cluster_name)
        cluster_info = platform.environment["clusters"].get(cluster_name, {})
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cluster_info["install_method"] = "az"
        cluster_info["hostedclusters"] = self.environment["cluster_count"]
        cluster_info["environment"] = self.environment["aro_env"]
        mc_name = os.environ.get("MC_NAME")
        if mc_name:
            cluster_info["mgmt_cluster_name"] = mc_name
        else:
            cluster_info["mgmt_cluster_name"] = ""

        # Ensure path is set for metadata file
        if "path" not in cluster_info:
            cluster_info["path"] = platform.environment["path"] + "/" + cluster_name

        customer_rg_name = cluster_info.get("resource_group") or self.environment.get("customer_rg_name") or f"{cluster_name}-rg"

        self.logging.info(f"[{cluster_name}] Deleting ARO HCP cluster from resource group {customer_rg_name}")

        # Start timing the deletion
        delete_start_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        cluster_info["delete_start_time"] = delete_start_time
        cluster_info["status"] = "Deleting"

        # Step 1: Delete the cluster resource first
        self.logging.info(f"[{cluster_name}] Step 1: Deleting ARO HCP cluster resource")
        try:
            # Use ResourceManagementClient to delete the cluster resource
            # Resource ID format: /subscriptions/{subscription}/resourceGroups/{rg}/providers/Microsoft.RedHatOpenShift/hcpOpenShiftClusters/{name}
            resource_id = f"/subscriptions/{self.environment.get('subscription_id')}/resourceGroups/{customer_rg_name}/providers/Microsoft.RedHatOpenShift/hcpOpenShiftClusters/{cluster_name}"

            # Use the generic resource deletion API
            delete_operation = self.resource_client.resources.begin_delete_by_id(
                resource_id=resource_id,
                api_version="2024-06-10-preview"
            )
            # Wait for deletion to complete
            delete_operation.wait()
            cluster_delete_end_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            self.logging.info(f"[{cluster_name}] Cluster resource deleted successfully")
        except HttpResponseError as err:
            if err.status_code == 404:
                self.logging.warning(f"[{cluster_name}] Cluster resource not found, may already be deleted")
            else:
                self.logging.warning(f"[{cluster_name}] Failed to delete cluster resource: {err}")
                self.logging.warning(f"[{cluster_name}] Continuing with resource group deletion")
        except Exception as err:
            self.logging.warning(f"[{cluster_name}] Unexpected error deleting cluster resource: {err}")
            self.logging.warning(f"[{cluster_name}] Continuing with resource group deletion")

        # Step 2: Delete resource group (this will delete all remaining resources)
        self.logging.info(f"[{cluster_name}] Step 2: Deleting resource group {customer_rg_name}")
        try:
            # Use begin_delete for async operation (no-wait equivalent)
            delete_operation = self.resource_client.resource_groups.begin_delete(
                resource_group_name=customer_rg_name
            )
            # Don't wait for completion (equivalent to --no-wait)
            self.logging.info(f"[{cluster_name}] Resource group {customer_rg_name} deletion initiated")

            # Calculate deletion time
            delete_end_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            cluster_info["destroy_duration"] = cluster_delete_end_time - delete_start_time
            cluster_info["destroy_all_duration"] = delete_end_time - delete_start_time
            cluster_info["status"] = "Deleted"

            self.logging.info(f"[{cluster_name}] Cluster deletion completed in {cluster_info['destroy_duration']} seconds")

            # Ensure directory exists and write metadata_destroy.json
            try:
                os.makedirs(cluster_info['path'], exist_ok=True)
                metadata_destroy_file = os.path.join(cluster_info['path'], "metadata_destroy.json")
                with open(metadata_destroy_file, "w") as metadata_file:
                    json.dump(cluster_info, metadata_file, indent=2)
                self.logging.info(f"[{cluster_name}] Metadata destroy file written to {metadata_destroy_file}")
            except Exception as err:
                self.logging.error(f"[{cluster_name}] Failed to write metadata_destroy.json: {err}")
                self.logging.error(f"[{cluster_name}] Attempted path: {cluster_info.get('path', 'N/A')}")

            # Index deletion metadata to Elasticsearch
            if self.es:
                try:
                    self.es.index_metadata(cluster_info)
                    self.logging.info(f"[{cluster_name}] Successfully indexed cluster deletion metadata to ES")
                except Exception as es_err:
                    self.logging.warning(f"[{cluster_name}] Failed to index deletion metadata to ES: {es_err}")

            return 0
        except HttpResponseError as err:
            self.logging.error(f"[{cluster_name}] Failed to delete resource group {customer_rg_name}: {err}")
            cluster_info["status"] = "Delete Failed"
            return 1
        except Exception as err:
            self.logging.error(f"[{cluster_name}] Unexpected error deleting resource group {customer_rg_name}: {err}")
            cluster_info["status"] = "Delete Failed"
            return 1

    def get_metadata(self, platform, cluster_name):
        metadata = super().get_metadata(platform, cluster_name)
        cluster_info = platform.environment["clusters"].get(cluster_name, {})
        customer_rg_name = cluster_info.get("resource_group") or self.environment.get("customer_rg_name") or f"{cluster_name}-rg"
        subscription_id = self.environment.get("subscription_id")

        self.logging.info(f"[{cluster_name}] Getting metadata for ARO HCP cluster")

        # Get cluster information from Azure REST API with retry logic
        api_version = "2024-06-10-preview"
        cluster_resource_id = f"/subscriptions/{subscription_id}/resourceGroups/{customer_rg_name}/providers/Microsoft.RedHatOpenShift/hcpOpenShiftClusters/{cluster_name}"
        cluster_url = f"https://management.azure.com{cluster_resource_id}?api-version={api_version}"

        max_retries = 3
        retry_delay = 5  # seconds
        azure_cluster_data = None

        for attempt in range(1, max_retries + 1):
            try:
                # Get access token
                token = self.credential.get_token("https://management.azure.com/.default").token

                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }

                self.logging.debug(f"[{cluster_name}] Attempting to get metadata (attempt {attempt}/{max_retries})")
                response = requests.get(cluster_url, headers=headers)
                response.raise_for_status()
                azure_cluster_data = response.json()
                self.logging.info(f"[{cluster_name}] Successfully retrieved metadata on attempt {attempt}")
                break  # Success, exit retry loop

            except requests.exceptions.RequestException as err:
                if attempt < max_retries:
                    self.logging.warning(f"[{cluster_name}] Error getting metadata (attempt {attempt}/{max_retries}): {err}")
                    self.logging.info(f"[{cluster_name}] Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    self.logging.error(f"[{cluster_name}] Failed to get metadata after {max_retries} attempts: {err}")
            except Exception as err:
                if attempt < max_retries:
                    self.logging.warning(f"[{cluster_name}] Unexpected error getting metadata (attempt {attempt}/{max_retries}): {err}")
                    self.logging.info(f"[{cluster_name}] Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    self.logging.error(f"[{cluster_name}] Unexpected error after {max_retries} attempts: {err}")

        # Process metadata if successfully retrieved
        if azure_cluster_data:
            properties = azure_cluster_data.get("properties", {})

            # Basic cluster information
            metadata["cluster_name"] = cluster_name
            metadata["cluster_id"] = azure_cluster_data.get("id", None)
            metadata["resource_group"] = customer_rg_name
            metadata["subscription_id"] = subscription_id
            metadata["location"] = azure_cluster_data.get("location", None)

            # Save provisioning state as status (similar to ROSA)
            provisioning_state = properties.get("provisioningState", None)
            metadata["status"] = provisioning_state
            metadata["provisioning_state"] = provisioning_state  # Keep both for compatibility

            # Version information
            version_info = properties.get("version", {})
            metadata["version"] = version_info.get("id", None)
            metadata["channel_group"] = version_info.get("channelGroup", None)

            # Domain and URL information
            domain_info = properties.get("domain", None)
            metadata["base_domain"] = domain_info
            metadata["api_url"] = properties.get("api", {}).get("url", None)
            metadata["console_url"] = properties.get("console", {}).get("url", None)

            # Azure region (equivalent to aws_region in ROSA)
            metadata["azure_region"] = azure_cluster_data.get("location", None)

            # Network information
            network_profile = properties.get("networkProfile", {})
            metadata["network_type"] = network_profile.get("type", None)
            metadata["pod_cidr"] = network_profile.get("podCidr", None)
            metadata["service_cidr"] = network_profile.get("serviceCidr", None)

            # Platform information
            platform_info = properties.get("platform", {})
            metadata["subnet_id"] = platform_info.get("subnetId", None)

            # Get deployment information from Azure
            try:
                deployment = self.resource_client.deployments.get(
                    resource_group_name=customer_rg_name,
                    deployment_name="aro-hcp"
                )
                metadata["deployment_name"] = "aro-hcp"
                if deployment.properties:
                    metadata["deployment_provisioning_state"] = deployment.properties.provisioning_state
            except HttpResponseError as err:
                if err.status_code == 404:
                    self.logging.warning(f"[{cluster_name}] Deployment 'aro-hcp' not found")
                else:
                    self.logging.warning(f"[{cluster_name}] Could not retrieve deployment metadata: {err}")
            except Exception as err:
                self.logging.warning(f"[{cluster_name}] Unexpected error retrieving deployment metadata: {err}")
        else:
            self.logging.error(f"[{cluster_name}] Failed to retrieve cluster metadata after all retry attempts")

        return metadata

    def download_kubeconfig(self, cluster_name, platform, external_auth_name=None, issuer_url=None, customer_rg_name=None):
        """
        Download kubeconfig for an ARO HCP cluster.

        Args:
            cluster_name: Name of the cluster
            platform: Platform object to access environment and cluster_info
            external_auth_name: Name for external auth (default: {cluster_name}-auth)
            issuer_url: OIDC issuer URL (required if creating external auth)
            customer_rg_name: Resource group name (default: from environment or {cluster_name}-rg)

        Returns:
            Path to the downloaded kubeconfig file
        """
        # Get path the same way as create_cluster function
        path = platform.environment["path"] + "/" + cluster_name

        if customer_rg_name is None:
            customer_rg_name = self.environment.get("customer_rg_name") or f"{cluster_name}-rg"

        if external_auth_name is None:
            external_auth_name = f"{cluster_name}-auth"

        subscription_id = self.environment.get("subscription_id")
        api_version = "2024-06-10-preview"

        # Ensure path exists
        os.makedirs(path, exist_ok=True)

        try:
            # Step 1: Get cluster info to get OAUTH_CALLBACK_URL and API_URL
            # Retry loop: console URL may take a few minutes to generate
            self.logging.info(f"[{cluster_name}] Step 1: Getting cluster information")
            cluster_resource_id = f"/subscriptions/{subscription_id}/resourceGroups/{customer_rg_name}/providers/Microsoft.RedHatOpenShift/hcpOpenShiftClusters/{cluster_name}"
            cluster_url = f"https://management.azure.com{cluster_resource_id}?api-version={api_version}"

            # Get access token
            token = self.credential.get_token("https://management.azure.com/.default").token

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            # Retry loop: wait up to 30 minutes for console URL to be available
            max_wait_time = 30 * 60  # 30 minutes in seconds
            check_interval = 60  # 1 minute in seconds
            start_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            console_url = ""
            api_url = ""
            cluster_info = None

            while int(datetime.datetime.now(datetime.timezone.utc).timestamp()) < start_time + max_wait_time:
                try:
                    response = requests.get(cluster_url, headers=headers)
                    response.raise_for_status()
                    cluster_info = response.json()

                    console_url = cluster_info.get("properties", {}).get("console", {}).get("url", "")
                    api_url = cluster_info.get("properties", {}).get("api", {}).get("url", "")

                    if console_url and console_url.startswith("http"):
                        self.logging.info(f"[{cluster_name}] Console URL is now available: {console_url}")
                        break
                    else:
                        elapsed = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - start_time
                        self.logging.info(f"[{cluster_name}] Console URL not yet available (elapsed: {elapsed}s), waiting {check_interval}s before retry...")
                        time.sleep(check_interval)
                except requests.exceptions.RequestException as err:
                    elapsed = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - start_time
                    self.logging.warning(f"[{cluster_name}] Error getting cluster info (elapsed: {elapsed}s): {err}, retrying in {check_interval}s...")
                    time.sleep(check_interval)

            # Check if we got a valid console URL
            if not console_url or not console_url.startswith("http"):
                elapsed = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - start_time
                raise Exception(f"Console URL not available after {elapsed}s (max wait: {max_wait_time}s). Cannot proceed with kubeconfig download.")

            oauth_callback_url = console_url.rstrip("/") + "/auth/callback"

            self.logging.info(f"[{cluster_name}] OAuth Callback URL: {oauth_callback_url}")
            self.logging.info(f"[{cluster_name}] API URL: {api_url}")

            # Step 2: Create AD App
            self.logging.info(f"[{cluster_name}] Step 2: Creating AD App {external_auth_name}")
            # Pass redirect URIs as separate arguments (Azure CLI accepts multiple values after the flag)
            ad_app_cmd = [
                "az", "ad", "app", "create",
                "--display-name", external_auth_name,
                "--web-redirect-uris", oauth_callback_url, "http://localhost:8000",
                "--query", "appId",
                "--output", "tsv"
            ]
            ad_app_result = subprocess.run(ad_app_cmd, capture_output=True, text=True, check=True)
            client_id = ad_app_result.stdout.strip()
            self.logging.info(f"[{cluster_name}] Created AD App with Client ID: {client_id}")

            # Step 3: Create AD App Secret
            self.logging.info(f"[{cluster_name}] Step 3: Creating AD App Secret")
            ad_secret_cmd = [
                "az", "ad", "app", "credential", "reset",
                "--id", client_id,
                "--query", "password",
                "--output", "tsv"
            ]
            ad_secret_result = subprocess.run(ad_secret_cmd, capture_output=True, text=True, check=True)
            client_secret = ad_secret_result.stdout.strip()
            self.logging.info(f"[{cluster_name}] AD App Secret created")

            # Step 4: Create External Auth Deployment
            if issuer_url:
                self.logging.info(f"[{cluster_name}] Step 4: Creating external auth deployment")
                external_auth_template_path = self._get_bicep_template_path("externalauth.bicep")

                # Compile Bicep template
                compiled_template_path = os.path.join(path, "externalauth.json")
                compile_cmd = f"az bicep build --file {shlex.quote(external_auth_template_path)} --outfile {shlex.quote(compiled_template_path)}"
                compile_result = subprocess.run(compile_cmd, shell=True, capture_output=True, text=True)

                if compile_result.returncode != 0:
                    self.logging.error(f"[{cluster_name}] Failed to compile externalauth.bicep: {compile_result.stderr}")
                    raise Exception(f"Bicep compilation failed: {compile_result.stderr}")

                with open(compiled_template_path, 'r') as f:
                    template_json = json.load(f)

                auth_parameters = {
                    "externalAuthName": {"value": external_auth_name},
                    "issuerURL": {"value": issuer_url},
                    "clientID": {"value": client_id},
                    "clusterName": {"value": cluster_name}
                }

                deployment_properties = DeploymentProperties(
                    mode=DeploymentMode.INCREMENTAL,
                    template=template_json,
                    parameters=auth_parameters
                )
                deployment = Deployment(properties=deployment_properties)

                auth_deployment_operation = self.resource_client.deployments.begin_create_or_update(
                    resource_group_name=customer_rg_name,
                    deployment_name="aro-hcp-auth",
                    parameters=deployment
                )
                auth_deployment_result = auth_deployment_operation.result()
                self.logging.info(f"[{cluster_name}] External auth deployment completed")

                # Save auth deployment result JSON to file
                auth_deployment_output_file = os.path.join(path, "auth-deployment-result.json")
                try:
                    with open(auth_deployment_output_file, 'w') as f:
                        json.dump(auth_deployment_result.as_dict(), f, indent=2, default=str)
                    self.logging.info(f"[{cluster_name}] Auth deployment result saved to {auth_deployment_output_file}")
                except Exception as save_err:
                    self.logging.warning(f"[{cluster_name}] Failed to save auth deployment result JSON: {save_err}")

                # Step 5: Wait for auth to be ready
                self.logging.info(f"[{cluster_name}] Step 5: Waiting 60 seconds for auth to be ready...")
                time.sleep(60)
            else:
                self.logging.warning(f"[{cluster_name}] Issuer URL not provided, skipping external auth deployment")

            # Step 6: Get Resource ID
            self.logging.info(f"[{cluster_name}] Step 6: Getting cluster resource ID")
            resource_id = cluster_resource_id
            self.logging.info(f"[{cluster_name}] Resource ID: {resource_id}")

            # Step 7: Request Admin Credential
            self.logging.info(f"[{cluster_name}] Step 7: Requesting admin credential")
            admin_cred_url = f"https://management.azure.com{resource_id}/requestadmincredential?api-version={api_version}"

            # Use requests with debug to capture Location header
            admin_response = requests.post(admin_cred_url, headers=headers, allow_redirects=False)
            admin_response.raise_for_status()

            # Extract Location header
            kubeconfig_url = admin_response.headers.get("Location")
            if not kubeconfig_url:
                self.logging.error(f"[{cluster_name}] Location header not found in admin credential response")
                raise Exception("Failed to get kubeconfig URL from admin credential response")

            self.logging.info(f"[{cluster_name}] Kubeconfig URL obtained: {kubeconfig_url[:50]}...")

            # Step 8: Wait before downloading
            self.logging.info(f"[{cluster_name}] Step 8: Waiting 60 seconds before downloading kubeconfig...")
            time.sleep(60)

            # Step 9: Download Kubeconfig
            self.logging.info(f"[{cluster_name}] Step 9: Downloading kubeconfig")
            kubeconfig_response = requests.get(kubeconfig_url, headers=headers)
            kubeconfig_response.raise_for_status()
            kubeconfig_data = kubeconfig_response.json()

            kubeconfig_content = kubeconfig_data.get("kubeconfig")
            if not kubeconfig_content:
                self.logging.error(f"[{cluster_name}] kubeconfig not found in response")
                raise Exception("Failed to get kubeconfig from response")

            # Save kubeconfig to file
            kubeconfig_path = os.path.join(path, "kubeconfig")
            with open(kubeconfig_path, "w") as kubeconfig_file:
                kubeconfig_file.write(kubeconfig_content)

            self.logging.info(f"[{cluster_name}] Kubeconfig downloaded successfully to {kubeconfig_path}")

            # Step 10: Configure external auth if issuer_url was provided
            if issuer_url and client_secret:
                try:
                    self.logging.info(f"Step 10: Configuring external auth in cluster {cluster_name}")
                    azure_ad_group_name = self.environment.get("azure_ad_group_name", "aro-hcp-perfscale")
                    self._configure_external_auth(
                        cluster_name=cluster_name,
                        kubeconfig_path=kubeconfig_path,
                        external_auth_name=external_auth_name,
                        client_secret=client_secret,
                        azure_ad_group_name=azure_ad_group_name
                    )
                    self.logging.info(f"External auth configuration completed for cluster {cluster_name}")
                except Exception as auth_err:
                    self.logging.warning(f"Failed to configure external auth for cluster {cluster_name}: {auth_err}")
                    self.logging.warning(f"Continuing despite external auth configuration failure for cluster {cluster_name}")

            return kubeconfig_path

        except subprocess.CalledProcessError as err:
            self.logging.error(f"[{cluster_name}] Azure CLI command failed: {err}")
            self.logging.error(f"[{cluster_name}] stdout: {err.stdout}")
            self.logging.error(f"[{cluster_name}] stderr: {err.stderr}")
            raise
        except requests.exceptions.RequestException as err:
            self.logging.error(f"[{cluster_name}] HTTP request failed: {err}")
            if hasattr(err, 'response') and err.response is not None:
                self.logging.error(f"[{cluster_name}] Response: {err.response.text}")
            raise
        except Exception as err:
            self.logging.error(f"[{cluster_name}] Unexpected error downloading kubeconfig: {err}")
            raise

    def _configure_external_auth(self, cluster_name, kubeconfig_path, external_auth_name, client_secret, azure_ad_group_name):
        """
        Configure external auth in the cluster by:
        1. Creating a Kubernetes secret with the client secret
        2. Getting the Azure AD group ID
        3. Creating a ClusterRoleBinding to grant cluster-admin to the Azure AD group

        Args:
            cluster_name: Name of the cluster
            kubeconfig_path: Path to the kubeconfig file
            external_auth_name: Name of the external auth (used for secret name)
            client_secret: Client secret from Azure AD app
            azure_ad_group_name: Name of the Azure AD group to grant cluster-admin access
        """
        # Load kubeconfig
        k8s_config.load_kube_config(config_file=kubeconfig_path)

        # Step 1: Create Kubernetes secret with client secret
        secret_name = f"{external_auth_name}-console-openshift-console"
        namespace = "openshift-config"

        self.logging.info(f"[{cluster_name}] Creating Kubernetes secret {secret_name} in {namespace} namespace")

        v1 = k8s_client.CoreV1Api()

        # Check if secret already exists, if so delete it first
        try:
            v1.read_namespaced_secret(name=secret_name, namespace=namespace)
            self.logging.info(f"[{cluster_name}] Secret already exists, deleting it first")
            v1.delete_namespaced_secret(name=secret_name, namespace=namespace)
            # Wait a moment for deletion to complete
            time.sleep(2)
        except ApiException as err:
            if err.status != 404:
                self.logging.warning(f"[{cluster_name}] Error checking for existing secret: {err}")

        # Create the secret (using string_data which automatically handles base64 encoding)
        secret_body = k8s_client.V1Secret(
            api_version="v1",
            kind="Secret",
            metadata=k8s_client.V1ObjectMeta(
                name=secret_name,
                namespace=namespace
            ),
            type="Opaque",
            string_data={
                "clientSecret": client_secret
            }
        )

        try:
            v1.create_namespaced_secret(namespace=namespace, body=secret_body)
            self.logging.info(f"[{cluster_name}] Kubernetes secret {secret_name} created successfully")
        except ApiException as err:
            if err.status == 409:
                # Secret might have been created between check and create, try to update it
                self.logging.info(f"[{cluster_name}] Secret exists, updating it")
                v1.replace_namespaced_secret(name=secret_name, namespace=namespace, body=secret_body)
            else:
                raise Exception(f"[{cluster_name}] Failed to create Kubernetes secret: {err}")

        # Step 2: Get Azure AD group ID
        self.logging.info(f"[{cluster_name}] Getting Azure AD group ID for group: {azure_ad_group_name}")
        group_cmd = [
            "az", "ad", "group", "show",
            "--group", azure_ad_group_name,
            "--query", "id",
            "--output", "tsv"
        ]
        group_result = subprocess.run(group_cmd, capture_output=True, text=True, check=True)
        group_id = group_result.stdout.strip()

        if not group_id:
            raise Exception(f"[{cluster_name}] Failed to get Azure AD group ID for group: {azure_ad_group_name}")

        self.logging.info(f"[{cluster_name}] Azure AD group ID: {group_id}")

        # Step 3: Create ClusterRoleBinding using Kubernetes client
        self.logging.info(f"[{cluster_name}] Creating ClusterRoleBinding for Azure AD group")
        rbac_v1 = k8s_client.RbacAuthorizationV1Api()

        cluster_role_binding_name = "aro-admins"

        # Check if ClusterRoleBinding already exists
        try:
            existing_binding = rbac_v1.read_cluster_role_binding(name=cluster_role_binding_name)
            self.logging.info(f"[{cluster_name}] ClusterRoleBinding already exists, updating it")
            # Update existing binding
            existing_binding.subjects = [
                k8s_client.V1Subject(
                    api_group="rbac.authorization.k8s.io",
                    kind="Group",
                    name=group_id
                )
            ]
            rbac_v1.replace_cluster_role_binding(name=cluster_role_binding_name, body=existing_binding)
            self.logging.info(f"[{cluster_name}] ClusterRoleBinding {cluster_role_binding_name} updated successfully")
        except ApiException as err:
            if err.status == 404:
                # Create new ClusterRoleBinding
                cluster_role_binding = k8s_client.V1ClusterRoleBinding(
                    api_version="rbac.authorization.k8s.io/v1",
                    kind="ClusterRoleBinding",
                    metadata=k8s_client.V1ObjectMeta(
                        name=cluster_role_binding_name
                    ),
                    role_ref=k8s_client.V1RoleRef(
                        api_group="rbac.authorization.k8s.io",
                        kind="ClusterRole",
                        name="cluster-admin"
                    ),
                    subjects=[
                        k8s_client.V1Subject(
                            api_group="rbac.authorization.k8s.io",
                            kind="Group",
                            name=group_id
                        )
                    ]
                )

                try:
                    rbac_v1.create_cluster_role_binding(body=cluster_role_binding)
                    self.logging.info(f"[{cluster_name}] ClusterRoleBinding {cluster_role_binding_name} created successfully")
                except ApiException as create_err:
                    raise Exception(f"[{cluster_name}] Failed to create ClusterRoleBinding: {create_err}")
            else:
                raise Exception(f"[{cluster_name}] Error checking for existing ClusterRoleBinding: {err}")

    def _build_nodepool_parameters(self, cluster_name, np_name, autoscale, replica=None, min_replica=None, max_replica=None, node_size="Standard_D8s_v3"):
        """Helper function to build nodepool parameters based on autoscale mode"""
        base_params = {
            "clusterName": {"value": cluster_name},
            "nodePoolName": {"value": np_name},
            "autoscale": {"value": autoscale},
            "nodeSize": {"value": node_size}
        }

        if autoscale:
            base_params.update({
                "minReplica": {"value": min_replica},
                "maxReplica": {"value": max_replica}
            })
        else:
            base_params.update({
                "replica": {"value": replica}
            })

        return base_params

    def _create_worker_nodepool(self, customer_rg_name, cluster_name, np_name, deployment_name, autoscale, replica, min_replica, max_replica, node_size, subscription_id, output_path=None):
        """Helper function to create a single worker nodepool"""
        template_name = "nodepool.bicep"  # Combined template handles both static and autoscale
        parameters = self._build_nodepool_parameters(
            cluster_name, np_name, autoscale, replica, min_replica, max_replica, node_size
        )
        replica_info = f"{min_replica}-{max_replica}" if autoscale else str(replica)
        self.logging.info(f"[{cluster_name}] Creating {np_name} with {replica_info} replicas")
        self._create_nodepool_deployment(cluster_name, customer_rg_name, deployment_name, template_name, parameters, subscription_id, wait=False, output_path=output_path)

    def create_nodepool(self, cluster_name, replica, max_replica=None, min_replica=None, node_size="Standard_D8s_v3", autoscale=False, customer_rg_name=None, add_aro_hcp_infra=None):
        """
        Create nodepools for an ARO HCP cluster.

        Args:
            cluster_name: Name of the cluster
            replica: Number of replicas (for static nodepools)
            max_replica: Maximum replicas (for autoscale nodepools, defaults to replica if not set)
            min_replica: Minimum replicas (for autoscale nodepools, defaults to 1 if not set)
            node_size: VM size for the nodepool (default: Standard_D8s_v3)
            autoscale: Whether to use autoscaling (default: False)
            customer_rg_name: Resource group name (default: from environment or {cluster_name}-rg)
            add_aro_hcp_infra: Whether to create infra nodepool (default: False)
        """
        if customer_rg_name is None:
            customer_rg_name = self.environment.get("customer_rg_name") or f"{cluster_name}-rg"

        if add_aro_hcp_infra is None:
            add_aro_hcp_infra = self.environment.get("add_aro_hcp_infra", False)

        subscription_id = self.environment.get("subscription_id")
        limit = 199  # Maximum replicas per nodepool is 200, keeping this as safe limit

        # Construct cluster path for saving deployment results
        cluster_path = os.path.join(self.environment.get("path", "/tmp"), cluster_name)

        # Set defaults
        max_replica = max_replica or replica
        min_replica = min_replica or 1

        self.logging.info(f"[{cluster_name}] Creating nodepools")
        self.logging.info(f"[{cluster_name}] Replica: {replica}, Max Replica: {max_replica}, Min Replica: {min_replica}, Autoscale: {autoscale}, Node Size: {node_size}")

        # Determine effective replica count
        effective_replica = max_replica if autoscale else replica
        needs_splitting = effective_replica > limit

        if needs_splitting:
            # Split into multiple nodepools
            iterations = effective_replica // limit
            adjusted_replica = effective_replica % limit
            np_prefix = "np-scale" if autoscale else "np-static"

            # Create full-size nodepools
            for i in range(1, iterations + 1):
                np_name = f"{np_prefix}-{i}"
                deployment_name = f"node-pool-{i}"
                self._create_worker_nodepool(
                    customer_rg_name, cluster_name, np_name, deployment_name,
                    autoscale, limit, min_replica, limit, node_size, subscription_id, output_path=cluster_path
                )

            # Create remaining nodepool if needed
            if adjusted_replica > 0:
                np_name = f"{np_prefix}-{iterations + 1}"
                deployment_name = f"node-pool-{iterations + 1}"
                self._create_worker_nodepool(
                    customer_rg_name, cluster_name, np_name, deployment_name,
                    autoscale, adjusted_replica, min_replica, adjusted_replica, node_size, subscription_id, output_path=cluster_path
                )
        else:
            # Create single nodepool
            np_name = "np-scale" if autoscale else "np-static"
            deployment_name = "node-pool-2" if autoscale else "node-pool"
            self._create_worker_nodepool(
                customer_rg_name, cluster_name, np_name, deployment_name,
                autoscale, replica, min_replica, max_replica, node_size, subscription_id, output_path=cluster_path
            )

        # Create infra nodepool if requested
        if add_aro_hcp_infra:
            self.logging.info(f"[{cluster_name}] Creating infra nodepool")
            np_name = "np-infra"
            deployment_name = "node-pool-infra"
            template_name = "nodepool-infra.bicep"
            infra_size = self.environment.get("infra_size", "Standard_E8s_v3")
            parameters = {
                "clusterName": {"value": cluster_name},
                "nodePoolName": {"value": np_name},
                "nodeSize": {"value": infra_size}
            }
            self.logging.info(f"[{cluster_name}] Creating infra nodepool with VM size: {infra_size}")
            self._create_nodepool_deployment(cluster_name, customer_rg_name, deployment_name, template_name, parameters, subscription_id, wait=True, output_path=cluster_path)

        self.logging.info(f"[{cluster_name}] Nodepool creation completed")

    def _create_nodepool_deployment(self, cluster_name, resource_group_name, deployment_name, template_name, parameters, subscription_id, wait=False, output_path=None):
        """Helper function to create a nodepool deployment from a Bicep template"""
        import subprocess

        bicep_template_path = self._get_bicep_template_path(template_name)

        # Compile Bicep template to JSON
        import tempfile
        compiled_template_path = os.path.join(tempfile.gettempdir(), f"{deployment_name}-{int(time.time())}.json")
        compile_cmd = f"az bicep build --file {shlex.quote(bicep_template_path)} --outfile {shlex.quote(compiled_template_path)}"
        compile_result = subprocess.run(compile_cmd, shell=True, capture_output=True, text=True)

        if compile_result.returncode != 0:
            self.logging.error(f"[{cluster_name}] Failed to compile Bicep template {template_name}: {compile_result.stderr}")
            raise Exception(f"Bicep compilation failed: {compile_result.stderr}")

        # Read compiled template
        with open(compiled_template_path, 'r') as f:
            template_json = json.load(f)

        # Create deployment
        deployment_properties = DeploymentProperties(
            mode=DeploymentMode.INCREMENTAL,
            template=template_json,
            parameters=parameters
        )
        deployment = Deployment(properties=deployment_properties)

        try:
            if wait:
                # Wait for completion
                deployment_operation = self.resource_client.deployments.begin_create_or_update(
                    resource_group_name=resource_group_name,
                    deployment_name=deployment_name,
                    parameters=deployment
                )
                deployment_result = deployment_operation.result()
                self.logging.info(f"[{cluster_name}] Deployment {deployment_name} completed successfully")

                # Save deployment result JSON to file if output_path is provided
                if output_path:
                    deployment_output_file = os.path.join(output_path, f"{deployment_name}-deployment-result.json")
                    try:
                        with open(deployment_output_file, 'w') as f:
                            json.dump(deployment_result.as_dict(), f, indent=2, default=str)
                        self.logging.info(f"[{cluster_name}] Nodepool deployment result saved to {deployment_output_file}")
                    except Exception as save_err:
                        self.logging.warning(f"[{cluster_name}] Failed to save nodepool deployment result JSON: {save_err}")
            else:
                # Start deployment without waiting (equivalent to --no-wait)
                deployment_operation = self.resource_client.deployments.begin_create_or_update(
                    resource_group_name=resource_group_name,
                    deployment_name=deployment_name,
                    parameters=deployment
                )
                self.logging.info(f"[{cluster_name}] Deployment {deployment_name} started (async)")
        except HttpResponseError as err:
            self.logging.error(f"[{cluster_name}] Failed to create deployment {deployment_name}: {err}")
            raise
        finally:
            # Clean up compiled template
            if os.path.exists(compiled_template_path):
                try:
                    os.remove(compiled_template_path)
                except OSError:
                    pass

    def _wait_for_infra_nodes(self, kubeconfig, cluster_name, expected_infra_nodes=2, wait_time=15):
        """
        Wait for infra nodes to be ready.

        Args:
            kubeconfig: Path to kubeconfig file
            cluster_name: Name of the cluster
            expected_infra_nodes: Number of infra nodes expected (default: 2)
            wait_time: Maximum wait time in minutes (default: 15)

        Returns:
            int: Number of ready infra nodes, or 0 if failed
        """
        self.logging.info(
            f"[{cluster_name}] Waiting {wait_time} minutes for {expected_infra_nodes} infra nodes to be ready"
        )
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        starting_time = datetime.datetime.now(datetime.timezone.utc).timestamp()

        while datetime.datetime.now(datetime.timezone.utc).timestamp() < starting_time + wait_time * 60:
            nodes_code, nodes_out, nodes_err = self.utils.subprocess_exec(
                "oc get nodes -o json",
                extra_params={"env": myenv, "universal_newlines": True},
                log_output=False
            )
            if nodes_code != 0:
                self.logging.warning(f"[{cluster_name}] Failed to get nodes, retrying in 15 seconds...")
                time.sleep(15)
                continue

            try:
                nodes_json = json.loads(nodes_out)
            except Exception as err:
                self.logging.error(f"[{cluster_name}] Cannot parse nodes JSON: {err}")
                time.sleep(15)
                continue

            nodes = nodes_json.get("items", [])

            # Count ready infra nodes (nodes with node-role.kubernetes.io/infra label and Ready condition)
            ready_infra_nodes = 0
            for node in nodes:
                labels = node.get("metadata", {}).get("labels", {})
                if "node-role.kubernetes.io/infra" in labels:
                    conditions = node.get("status", {}).get("conditions", [])
                    for condition in conditions:
                        if condition.get("type") == "Ready" and condition.get("status") == "True":
                            ready_infra_nodes += 1
                            break

            if ready_infra_nodes >= expected_infra_nodes:
                self.logging.info(
                    f"[{cluster_name}] Found {ready_infra_nodes}/{expected_infra_nodes} ready infra nodes. Infra nodes are ready."
                )
                return ready_infra_nodes
            else:
                self.logging.info(
                    f"[{cluster_name}] Found {ready_infra_nodes}/{expected_infra_nodes} ready infra nodes. Waiting 15 seconds..."
                )
                time.sleep(15)

        self.logging.error(
            f"[{cluster_name}] Timeout waiting for infra nodes. Only {ready_infra_nodes}/{expected_infra_nodes} ready."
        )
        return ready_infra_nodes

    def _move_infra_components(self, kubeconfig, cluster_name):
        """
        Move infrastructure components (monitoring and ingress) to infra nodes.

        This function:
        1. Patches the IngressController to schedule router pods on infra nodes
        2. Creates/updates the cluster-monitoring-config ConfigMap to schedule
           Prometheus and Alertmanager on infra nodes

        Args:
            kubeconfig: Path to kubeconfig file
            cluster_name: Name of the cluster

        Returns:
            bool: True if successful, False otherwise
        """
        self.logging.info(f"[{cluster_name}] Moving infrastructure components to infra nodes")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig

        success = True

        # 1. Patch IngressController to use infra nodes (using patch file to avoid shell quoting issues)
        self.logging.info(f"[{cluster_name}] Patching IngressController to use infra nodes")
        ingress_patch_path = self._get_bicep_template_path("ingress-infra-patch.yaml")

        if not os.path.exists(ingress_patch_path):
            self.logging.error(f"[{cluster_name}] Ingress patch file not found: {ingress_patch_path}")
            success = False
        else:
            patch_code, patch_out, patch_err = self.utils.subprocess_exec(
                f"oc patch ingresscontroller/default -n openshift-ingress-operator --type=merge --patch-file={ingress_patch_path}",
                extra_params={"env": myenv, "universal_newlines": True}
            )
            if patch_code != 0:
                self.logging.error(f"[{cluster_name}] Failed to patch IngressController: {patch_err}")
                success = False
            else:
                self.logging.info(f"[{cluster_name}] IngressController patched successfully")

        # 2. Create/update cluster-monitoring-config ConfigMap from YAML file
        self.logging.info(f"[{cluster_name}] Configuring monitoring stack to use infra nodes")
        monitoring_config_path = self._get_bicep_template_path("cluster-monitoring-config.yaml")

        if not os.path.exists(monitoring_config_path):
            self.logging.error(f"[{cluster_name}] Monitoring config file not found: {monitoring_config_path}")
            success = False
        else:
            apply_code, apply_out, apply_err = self.utils.subprocess_exec(
                f"oc apply -f {monitoring_config_path}",
                extra_params={"env": myenv, "universal_newlines": True}
            )
            if apply_code != 0:
                self.logging.error(f"[{cluster_name}] Failed to apply monitoring config: {apply_err}")
                success = False
            else:
                self.logging.info(f"[{cluster_name}] Monitoring config applied successfully")

        if success:
            self.logging.info(f"[{cluster_name}] Infrastructure components successfully configured to use infra nodes")
        else:
            self.logging.warning(f"[{cluster_name}] Some infrastructure components may not have been configured correctly")

        return success

    def platform_cleanup(self):
        super().platform_cleanup()

    def _wait_for_workers(
        self, kubeconfig, worker_nodes, wait_time, cluster_name, machinepool_name
    ):
        """
        Wait for worker nodes to be ready in a specific machinepool.

        Args:
            kubeconfig: Path to kubeconfig file
            worker_nodes: Number of worker nodes expected
            wait_time: Maximum wait time in minutes
            cluster_name: Name of the cluster
            machinepool_name: Name of the machinepool/nodepool

        Returns:
            List containing [machinepool_name, ready_nodes_count, timestamp]
        """
        self.logging.info(
            f"[{cluster_name}] Waiting {wait_time} minutes for {worker_nodes} workers to be ready on {machinepool_name} machinepool"
        )
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        result = [machinepool_name]
        starting_time = datetime.datetime.now(datetime.timezone.utc).timestamp()
        self.logging.debug(
            f"[{cluster_name}] Waiting {wait_time} minutes for nodes to be Ready until {datetime.datetime.fromtimestamp(starting_time + wait_time * 60)}"
        )
        while datetime.datetime.now(datetime.timezone.utc).timestamp() < starting_time + wait_time * 60:
            if self.utils.force_terminate:
                self.logging.error(f"[{cluster_name}] Exiting workers waiting after capturing Ctrl-C")
                result.append(0)
                result.append("")
                return result
            self.logging.info(f"[{cluster_name}] Getting node information")
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
                    f"[{cluster_name}] Found {ready_nodes}/{worker_nodes} ready nodes on machinepool {machinepool_name}. Stopping wait."
                )
                result.append(ready_nodes)
                result.append(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
                return result
            else:
                self.logging.info(
                    f"[{cluster_name}] Found {ready_nodes}/{worker_nodes} ready nodes on machinepool {machinepool_name}. Waiting 15 seconds for next check..."
                )
                time.sleep(15)
        self.logging.error(
            f"[{cluster_name}] Waiting time expired. After {wait_time} minutes there are {ready_nodes}/{worker_nodes} ready nodes on {machinepool_name} machinepool"
        )
        result.append(ready_nodes)
        result.append("")
        return result

    def get_workers_ready(self, kubeconfig, cluster_name):
        super().get_workers_ready(kubeconfig, cluster_name)
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        self.logging.info(f"[{cluster_name}] Getting node information for Hypershift cluster")
        nodes_code, nodes_out, nodes_err = self.utils.subprocess_exec("oc get nodes -o json", extra_params={"env": myenv, "universal_newlines": True}, log_output=False)
        try:
            nodes_json = json.loads(nodes_out)
        except Exception as err:
            self.logging.debug(f"[{cluster_name}] Cannot load command result")
            self.logging.debug(f"[{cluster_name}] {err}")
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

    def watcher(self):
        super().watcher()


class HypershiftArguments(AroArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--customer-rg-name", action=EnvDefault, env=environment, envvar="HCP_BURNER_CUSTOMER_RG_NAME", help="Customer resource group name (default: {cluster_name}-rg)")
        parser.add_argument("--ticket-id", action=EnvDefault, env=environment, envvar="HCP_BURNER_TICKET_ID", default="default", help="Ticket ID for resource tags (default: default)")
        parser.add_argument("--customer-nsg", action=EnvDefault, env=environment, envvar="HCP_BURNER_CUSTOMER_NSG", help="Customer Network Security Group name (default: {cluster_name}-nsg)")
        parser.add_argument("--customer-vnet-name", action=EnvDefault, env=environment, envvar="HCP_BURNER_CUSTOMER_VNET_NAME", help="Customer Virtual Network name (default: {cluster_name}-vnet)")
        parser.add_argument("--customer-vnet-subnet1", action=EnvDefault, env=environment, envvar="HCP_BURNER_CUSTOMER_VNET_SUBNET1", help="Customer Virtual Network Subnet 1 name (default: {cluster_name}-subnet1)")
        parser.add_argument("--managed-resource-group", action=EnvDefault, env=environment, envvar="HCP_BURNER_MANAGED_RESOURCE_GROUP", help="Managed resource group name for the HCP cluster (default: {cluster_name}-managed-rg)")
        parser.add_argument("--worker-size", action=EnvDefault, env=environment, envvar="HCP_BURNER_WORKER_SIZE", default="Standard_D4s_v3", help="Azure VM size for worker nodes (default: Standard_D4s_v3)")
        parser.add_argument("--infra-size", action=EnvDefault, env=environment, envvar="HCP_BURNER_INFRA_SIZE", default="Standard_E8s_v3", help="Azure VM size for infra nodes (default: Standard_E8s_v3)")
        parser.add_argument("--autoscale", action="store_true", help="Enable autoscaling for worker nodepools")
        parser.add_argument("--max-replicas", action=EnvDefault, env=environment, envvar="HCP_BURNER_MAX_REPLICAS", type=int, help="Maximum number of worker replicas for autoscaling (required if --autoscale is set)")
        parser.add_argument("--min-replicas", action=EnvDefault, env=environment, envvar="HCP_BURNER_MIN_REPLICAS", type=int, default=1, help="Minimum number of worker replicas for autoscaling (default: 1)")
        parser.add_argument("--add-aro-hcp-infra", action=EnvDefault, env=environment, envvar="HCP_BURNER_ADD_ARO_HCP_INFRA", type=str, default="False", help="Create infra nodepool for ARO HCP cluster (default: False). Accepts: true/false, 1/0, yes/no")
        parser.add_argument("--azure-ad-group-name", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_AD_GROUP_NAME", default="aro-hcp-perfscale", help="Azure AD group name to grant cluster-admin access (default: aro-hcp-perfscale)")
        parser.add_argument("--issuer-url", action=EnvDefault, env=environment, envvar="HCP_BURNER_ISSUER_URL", default=None, help="OIDC issuer URL for external auth (default: https://login.microsoftonline.com/{tenant_id}/v2.0)")
        parser.add_argument("--azure-prom-token-file", action=EnvDefault, env=environment, envvar="HCP_BURNER_AZURE_PROM_TOKEN_FILE", help="Path to AZURE_PROM_TOKEN file for scraping metrics from MC (Management Cluster)")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Aro:Hypershift")))
            parser.set_defaults(**defaults)

        temp_args, temp_unknown_args = parser.parse_known_args()

    class EnvDefault(argparse.Action):
        def __init__(self, env, envvar, default=None, **kwargs):
            default = env[envvar] if envvar in env else default
            super(HypershiftArguments.EnvDefault, self).__init__(
                default=default, **kwargs
            )

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
