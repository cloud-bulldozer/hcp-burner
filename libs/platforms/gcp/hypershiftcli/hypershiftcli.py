#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import json
import os
import time
import datetime
import base64
import configparser
import concurrent.futures
import subprocess

from libs.platforms.gcp.gcp import Gcp
from libs.platforms.gcp.gcp import GcpArguments


class Hypershiftcli(Gcp):
    def __init__(self, arguments, logging, utils, es):
        super().__init__(arguments, logging, utils, es)

        self.environment["commands"].append("kubectl")
        self.environment["commands"].append("hypershift")
        self.environment["commands"].append("openssl")
        self.environment["mc_kubeconfig"] = arguments["mc_kubeconfig"]
        self.environment["release_image"] = arguments["release_image"]
        self.environment["pull_secret_path"] = arguments["pull_secret_path"]
        self.environment["base_domain"] = arguments["base_domain"]
        self.environment["hc_namespace"] = arguments["hc_namespace"]
        self.environment["feature_set"] = arguments["feature_set"]
        self.environment["endpoint_access"] = arguments["endpoint_access"]
        self.environment["disable_capabilities"] = arguments["disable_capabilities"]

    def initialize(self):
        super().initialize()
        self.logging.info(f"Verifying access to MC cluster using {self.environment['mc_kubeconfig']}...")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        code, _, _ = self.utils.subprocess_exec(
            "kubectl get hostedclusters -A",
            extra_params={"env": myenv, "universal_newlines": True},
            log_output=False
        )
        if code != 0:
            self.logging.error(f"Failed to access MC cluster using {self.environment['mc_kubeconfig']}")
            sys.exit("Exiting...")
        self.logging.info("MC cluster access verified")

    def platform_cleanup(self):
        super().platform_cleanup()

    def watcher(self):
        super().watcher()
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        ns = self.environment["hc_namespace"]
        self.logging.info(f"Watcher started | Expected: {self.environment['cluster_count']} clusters")
        file_path = os.path.join(self.environment["path"], "terminate_watcher")
        if os.path.exists(file_path):
            os.remove(file_path)
        while not self.utils.force_terminate:
            if os.path.isfile(file_path):
                self.logging.warning("Watcher manually terminated")
                break
            code, out, _ = self.utils.subprocess_exec(
                f"oc get hostedcluster -n {ns} -o json",
                extra_params={"env": myenv, "universal_newlines": True}
            )
            try:
                clusters = json.loads(out).get("items", [])
            except (ValueError, TypeError):
                self.logging.error("Failed to parse hosted cluster list")
                time.sleep(self.environment["watcher_delay"])
                continue

            current = 0
            completed = 0
            state = {}
            for cluster in clusters:
                name = cluster.get("metadata", {}).get("name", "")
                if self.environment["cluster_name_seed"] not in name:
                    continue
                current += 1
                s = cluster.get("status", {}).get("version", {}).get("history", [{}])[0].get("state", "")
                state[s] = state.get(s, 0) + 1
                if s == "Completed":
                    completed += 1

            self.logging.info(f"Clusters: {current}/{self.environment['cluster_count']} | {state}")
            if completed == self.environment["cluster_count"]:
                self.logging.info("All clusters Completed. Exiting watcher")
                break
            time.sleep(self.environment["watcher_delay"])
        self.logging.info("Watcher terminated")

    def get_metadata(self, platform, cluster_name):
        metadata = super().get_metadata(platform, cluster_name)
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        ns = self.environment["hc_namespace"]
        code, out, _ = self.utils.subprocess_exec(
            f"oc get hostedcluster {cluster_name} -n {ns} -o json",
            extra_params={"env": myenv, "universal_newlines": True},
            log_output=False
        )
        try:
            result = json.loads(out)
        except (ValueError, TypeError):
            self.logging.error(f"Cannot load metadata for cluster {cluster_name}")
            metadata["status"] = "not found"
            return metadata
        metadata["cluster_name"] = result.get("metadata", {}).get("name")
        metadata["cluster_id"] = result.get("spec", {}).get("clusterID")
        metadata["network_type"] = result.get("spec", {}).get("networking", {}).get("networkType")
        metadata["version"] = result.get("spec", {}).get("release", {}).get("image")
        metadata["status"] = result.get("status", {}).get("version", {}).get("history", [{}])[0].get("state")
        return metadata

    def download_kubeconfig(self, cluster_name, path):
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        ns = self.environment["hc_namespace"]
        self.logging.info(f"[{cluster_name}] Downloading kubeconfig from MC")
        start = datetime.datetime.utcnow().timestamp()
        while datetime.datetime.utcnow().timestamp() < start + 300:
            if self.utils.force_terminate:
                return None
            code, out, _ = self.utils.subprocess_exec(
                f"oc get secret -n {ns} {cluster_name}-admin-kubeconfig -o json",
                extra_params={"env": myenv, "universal_newlines": True}
            )
            if code == 0:
                try:
                    data = json.loads(out).get("data", {}).get("kubeconfig")
                    kubeconfig = base64.b64decode(data).decode("utf-8")
                    kubeconfig_path = os.path.join(path, "kubeconfig")
                    with open(kubeconfig_path, "w") as f:
                        f.write(kubeconfig)
                    self.logging.info(f"[{cluster_name}] Kubeconfig saved to {kubeconfig_path}")
                    return kubeconfig_path
                except Exception as err:
                    self.logging.warning(f"[{cluster_name}] Cannot parse kubeconfig secret: {err}")
            time.sleep(5)
        self.logging.error(f"[{cluster_name}] Failed to download kubeconfig after 5 minutes")
        return None

    def wait_for_controlplane_ready(self, cluster_name, wait_time):
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        ns = self.environment["hc_namespace"]
        start = datetime.datetime.utcnow().timestamp()
        while datetime.datetime.utcnow().timestamp() < start + wait_time * 60:
            if self.utils.force_terminate:
                return 0
            code, out, _ = self.utils.subprocess_exec(
                f"oc get hostedcluster -n {ns} {cluster_name} -o json",
                extra_params={"env": myenv, "universal_newlines": True}
            )
            try:
                conditions = json.loads(out).get("status", {}).get("conditions", [])
            except (ValueError, TypeError):
                time.sleep(5)
                continue
            if any(c.get("message") == "The hosted control plane is available" and c.get("status") == "True" for c in conditions):
                elapsed = int(datetime.datetime.utcnow().timestamp() - start)
                self.logging.info(f"[{cluster_name}] Control plane ready after {elapsed}s")
                return elapsed
            time.sleep(1)
        return 0

    def wait_for_cluster_ready(self, cluster_name, wait_time):
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = self.environment["mc_kubeconfig"]
        ns = self.environment["hc_namespace"]
        start = datetime.datetime.utcnow().timestamp()
        while datetime.datetime.utcnow().timestamp() < start + wait_time * 60:
            if self.utils.force_terminate:
                return 0
            code, out, _ = self.utils.subprocess_exec(
                f"oc get hostedcluster -n {ns} {cluster_name} -o json",
                extra_params={"env": myenv, "universal_newlines": True}
            )
            try:
                status = json.loads(out).get("status", {}).get("version", {}).get("history", [{}])[0].get("state")
            except (ValueError, TypeError):
                time.sleep(5)
                continue
            if status == "Completed":
                elapsed = int(datetime.datetime.utcnow().timestamp() - start)
                self.logging.info(f"[{cluster_name}] Cluster Completed after {elapsed}s")
                return elapsed
            self.logging.info(f"[{cluster_name}] Status: {status}, waiting...")
            time.sleep(15)
        return 0

    def _wait_for_workers(self, kubeconfig, worker_nodes, wait_time, cluster_name):
        self.logging.info(f"[{cluster_name}] Waiting {wait_time}min for {worker_nodes} workers")
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        start = int(datetime.datetime.utcnow().timestamp())
        while datetime.datetime.utcnow().timestamp() < start + wait_time * 60:
            if self.utils.force_terminate:
                return 0
            code, out, _ = self.utils.subprocess_exec(
                "oc get nodes -o json",
                extra_params={"env": myenv, "universal_newlines": True},
                log_output=False
            )
            try:
                nodes = json.loads(out).get("items", [])
            except (ValueError, TypeError):
                time.sleep(15)
                continue
            ready = sum(
                1 for node in nodes
                if any(c.get("type") == "Ready" and c.get("status") == "True"
                       for c in node.get("status", {}).get("conditions", []))
            )
            if ready >= worker_nodes:
                elapsed = int(datetime.datetime.utcnow().timestamp()) - start
                self.logging.info(f"[{cluster_name}] {ready}/{worker_nodes} workers ready in {elapsed}s")
                return elapsed
            self.logging.info(f"[{cluster_name}] {ready}/{worker_nodes} workers ready, waiting...")
            time.sleep(15)
        self.logging.error(f"[{cluster_name}] Timeout waiting for workers")
        return 0

    def _load_hypershift_json_output(self, path, required_keys):
        """Parse hypershift create iam/infra output.

        hypershift writes NDJSON logs and a pretty-printed result object to the
        same stream; pick the last JSON object that contains required_keys.
        """
        with open(path, "r") as f:
            content = f.read()
        decoder = json.JSONDecoder()
        idx = 0
        match = None
        while idx < len(content):
            while idx < len(content) and content[idx].isspace():
                idx += 1
            if idx >= len(content):
                break
            try:
                obj, end = decoder.raw_decode(content, idx)
            except json.JSONDecodeError:
                next_brace = content.find("{", idx + 1)
                if next_brace < 0:
                    break
                idx = next_brace
                continue
            idx = end
            if isinstance(obj, dict) and all(k in obj for k in required_keys):
                match = obj
        if match is None:
            raise ValueError(
                f"No JSON object with keys {required_keys} found in {path}"
            )
        return match

    def _generate_keys_and_jwks(self, cluster_path):
        """Generate RSA keypair and JWKS file for OIDC provider."""
        key_path = os.path.join(cluster_path, "sa-signer.key")
        pub_path = os.path.join(cluster_path, "sa-signer.pub")
        jwks_path = os.path.join(cluster_path, "jwks.json")

        self.utils.subprocess_exec(f"openssl genrsa -traditional -out {key_path} 4096")
        self.utils.subprocess_exec(f"openssl rsa -in {key_path} -pubout -out {pub_path}")

        hex_mod_cmd = (
            f"openssl rsa -in {key_path} -pubout -outform DER 2>/dev/null | "
            f"openssl rsa -pubin -inform DER -text -noout 2>/dev/null | "
            f"grep -A 100 '^Modulus:' | grep -v '^Modulus:' | grep -v '^Exponent:' | "
            f"tr -d ' \\n:' | sed 's/^00//'"
        )
        hex_mod = subprocess.run(hex_mod_cmd, shell=True, capture_output=True, text=True).stdout.strip()

        mod_cmd = f"printf '%b' \"$(echo '{hex_mod}' | sed 's/../\\\\x&/g')\" | base64 -w0 | tr '+/' '-_' | tr -d '='"
        modulus = subprocess.run(mod_cmd, shell=True, capture_output=True, text=True).stdout.strip()

        kid_cmd = (
            f"openssl rsa -in {key_path} -pubout -outform DER 2>/dev/null | "
            f"openssl dgst -sha256 -binary | base64 -w0 | tr '+/' '-_' | tr -d '='"
        )
        kid = subprocess.run(kid_cmd, shell=True, capture_output=True, text=True).stdout.strip()

        jwks = {
            "keys": [{
                "kty": "RSA", "alg": "RS256", "use": "sig",
                "kid": kid, "n": modulus, "e": "AQAB"
            }]
        }
        with open(jwks_path, "w") as f:
            json.dump(jwks, f, indent=2)
        self.logging.info("RSA keypair and JWKS generated")
        return key_path, jwks_path

    def _normalize_dns_name(self, name):
        return name.rstrip(".").lower()

    def _find_parent_dns_zone(self, parent_domain, project_id):
        """Find Cloud DNS managed zone whose dnsName matches parent_domain."""
        parent_fqdn = self._normalize_dns_name(parent_domain) + "."
        code, out, _ = self.utils.subprocess_exec(
            f"gcloud dns managed-zones list --project={project_id} --format=json",
            extra_params={"universal_newlines": True},
            log_output=False
        )
        if code != 0:
            self.logging.error(f"Failed to list Cloud DNS managed zones in project {project_id}")
            return None
        try:
            zones = json.loads(out or "[]")
        except (ValueError, TypeError):
            self.logging.error("Failed to parse Cloud DNS managed zones list")
            return None
        for zone in zones:
            if self._normalize_dns_name(zone.get("dnsName", "")) + "." == parent_fqdn:
                return zone.get("name")
        self.logging.error(
            f"No Cloud DNS managed zone found for parent domain {parent_fqdn} in project {project_id}"
        )
        return None

    def _ensure_cluster_dns(self, cluster_name, parent_domain, project_id):
        """
        Create child Cloud DNS zone named after the cluster and NS-delegate it
        from the parent zone that owns parent_domain.

        Returns child DNS name without trailing dot, e.g. cluster.gcp.example.org
        """
        parent_domain = self._normalize_dns_name(parent_domain)
        child_zone = cluster_name
        child_dns = f"{cluster_name}.{parent_domain}"
        child_fqdn = child_dns + "."

        self.logging.info(
            f"[{cluster_name}] Ensuring Cloud DNS zone {child_zone} for {child_fqdn}"
        )

        parent_zone = self._find_parent_dns_zone(parent_domain, project_id)
        if not parent_zone:
            return None

        # Create child zone if missing
        describe_code, _, _ = self.utils.subprocess_exec(
            f"gcloud dns managed-zones describe {child_zone} --project={project_id}",
            log_output=False
        )
        if describe_code != 0:
            # Pass as a list so description spaces are not broken by command.split()
            create_code, _, _ = self.utils.subprocess_exec(
                [
                    "gcloud", "dns", "managed-zones", "create", child_zone,
                    f"--dns-name={child_fqdn}",
                    f"--description=HyperShift HCP base domain for {cluster_name}",
                    "--visibility=public",
                    f"--project={project_id}",
                ]
            )
            if create_code != 0:
                self.logging.error(f"[{cluster_name}] Failed to create Cloud DNS zone {child_zone}")
                return None
            self.logging.info(f"[{cluster_name}] Created Cloud DNS zone {child_zone}")
        else:
            self.logging.info(f"[{cluster_name}] Cloud DNS zone {child_zone} already exists")

        # Child name servers
        ns_code, ns_out, _ = self.utils.subprocess_exec(
            f"gcloud dns managed-zones describe {child_zone} --project={project_id} "
            f"--format=value(nameServers)",
            extra_params={"universal_newlines": True},
            log_output=False
        )
        if ns_code != 0 or not ns_out:
            self.logging.error(f"[{cluster_name}] Failed to get name servers for zone {child_zone}")
            return None
        name_servers = [ns.strip() for ns in ns_out.replace(";", "\n").split() if ns.strip()]
        if not name_servers:
            self.logging.error(f"[{cluster_name}] Empty name server list for zone {child_zone}")
            return None

        # Add NS delegation in parent if missing (retry on 412 SOA precondition races)
        if self._ns_delegation_exists(parent_zone, child_fqdn, project_id):
            self.logging.info(f"[{cluster_name}] NS delegation already present in {parent_zone}")
        else:
            self.logging.info(
                f"[{cluster_name}] Adding NS delegation for {child_fqdn} in parent zone {parent_zone}"
            )
            if not self._add_ns_delegation(
                cluster_name, parent_zone, child_fqdn, name_servers, project_id
            ):
                return None
            self.logging.info(f"[{cluster_name}] NS delegation added for {child_fqdn}")

        return child_dns

    def _dns_tx_file(self, zone, cluster_name):
        """Per-cluster transaction file so parallel creates do not share transaction.yaml."""
        return os.path.join(
            "/tmp", f"hcp-burner-dns-tx-{zone}-{cluster_name}.yaml"
        )

    def _abort_dns_transaction(self, zone, project_id, tx_file):
        self.utils.subprocess_exec(
            [
                "gcloud", "dns", "record-sets", "transaction", "abort",
                f"--zone={zone}",
                f"--project={project_id}",
                f"--transaction-file={tx_file}",
            ],
            log_output=False,
        )
        try:
            os.remove(tx_file)
        except OSError:
            pass

    def _ns_delegation_exists(self, parent_zone, child_fqdn, project_id):
        code, out, _ = self.utils.subprocess_exec(
            f"gcloud dns record-sets list --zone={parent_zone} --project={project_id} "
            f"--name={child_fqdn} --type=NS --format=json",
            extra_params={"universal_newlines": True},
            log_output=False,
        )
        if code != 0 or not out:
            return False
        try:
            existing = json.loads(out)
        except (ValueError, TypeError):
            return False
        return bool(existing)

    def _add_ns_delegation(
        self, cluster_name, parent_zone, child_fqdn, name_servers, project_id, max_retries=5
    ):
        """Add NS records with retries for concurrent SOA/etag 412 precondition failures."""
        tx_file = self._dns_tx_file(parent_zone, cluster_name)
        for attempt in range(1, max_retries + 1):
            if self._ns_delegation_exists(parent_zone, child_fqdn, project_id):
                self.logging.info(
                    f"[{cluster_name}] NS delegation already present after attempt {attempt}"
                )
                return True

            self._abort_dns_transaction(parent_zone, project_id, tx_file)

            tx_start, _, _ = self.utils.subprocess_exec(
                [
                    "gcloud", "dns", "record-sets", "transaction", "start",
                    f"--zone={parent_zone}",
                    f"--project={project_id}",
                    f"--transaction-file={tx_file}",
                ],
                log_output=False,
            )
            if tx_start != 0:
                self.logging.warning(
                    f"[{cluster_name}] DNS transaction start failed "
                    f"(attempt {attempt}/{max_retries})"
                )
                time.sleep(min(2 ** attempt, 16))
                continue

            tx_add, _, _ = self.utils.subprocess_exec(
                [
                    "gcloud", "dns", "record-sets", "transaction", "add",
                    *name_servers,
                    f"--name={child_fqdn}",
                    "--ttl=300",
                    "--type=NS",
                    f"--zone={parent_zone}",
                    f"--project={project_id}",
                    f"--transaction-file={tx_file}",
                ],
                log_output=False,
            )
            if tx_add != 0:
                self._abort_dns_transaction(parent_zone, project_id, tx_file)
                self.logging.warning(
                    f"[{cluster_name}] DNS transaction add failed "
                    f"(attempt {attempt}/{max_retries})"
                )
                time.sleep(min(2 ** attempt, 16))
                continue

            tx_exec, _, _ = self.utils.subprocess_exec(
                [
                    "gcloud", "dns", "record-sets", "transaction", "execute",
                    f"--zone={parent_zone}",
                    f"--project={project_id}",
                    f"--transaction-file={tx_file}",
                ],
                log_output=False,
            )
            if tx_exec == 0:
                try:
                    os.remove(tx_file)
                except OSError:
                    pass
                return True

            # 412 Precondition / concurrent write: abort, re-check, retry
            self._abort_dns_transaction(parent_zone, project_id, tx_file)
            if self._ns_delegation_exists(parent_zone, child_fqdn, project_id):
                self.logging.info(
                    f"[{cluster_name}] NS delegation present after failed execute "
                    f"(likely concurrent writer succeeded)"
                )
                return True
            self.logging.warning(
                f"[{cluster_name}] DNS transaction execute failed "
                f"(attempt {attempt}/{max_retries}); retrying after backoff"
            )
            time.sleep(min(2 ** attempt, 16))

        self.logging.error(
            f"[{cluster_name}] Failed to add NS delegation for {child_fqdn} "
            f"after {max_retries} attempts"
        )
        return False

    def _remove_ns_delegation(
        self, cluster_name, parent_zone, child_fqdn, rrdatas, ttl, project_id, max_retries=5
    ):
        """Remove NS records with retries for concurrent SOA/etag 412 precondition failures."""
        tx_file = self._dns_tx_file(parent_zone, f"{cluster_name}-del")
        for attempt in range(1, max_retries + 1):
            if not self._ns_delegation_exists(parent_zone, child_fqdn, project_id):
                self.logging.info(f"[{cluster_name}] NS delegation already removed")
                return True

            self._abort_dns_transaction(parent_zone, project_id, tx_file)

            tx_start, _, _ = self.utils.subprocess_exec(
                [
                    "gcloud", "dns", "record-sets", "transaction", "start",
                    f"--zone={parent_zone}",
                    f"--project={project_id}",
                    f"--transaction-file={tx_file}",
                ],
                log_output=False,
            )
            if tx_start != 0:
                time.sleep(min(2 ** attempt, 16))
                continue

            rem_code, _, _ = self.utils.subprocess_exec(
                [
                    "gcloud", "dns", "record-sets", "transaction", "remove",
                    *rrdatas,
                    f"--name={child_fqdn}",
                    f"--ttl={ttl}",
                    "--type=NS",
                    f"--zone={parent_zone}",
                    f"--project={project_id}",
                    f"--transaction-file={tx_file}",
                ],
                log_output=False,
            )
            if rem_code != 0:
                self._abort_dns_transaction(parent_zone, project_id, tx_file)
                time.sleep(min(2 ** attempt, 16))
                continue

            tx_exec, _, _ = self.utils.subprocess_exec(
                [
                    "gcloud", "dns", "record-sets", "transaction", "execute",
                    f"--zone={parent_zone}",
                    f"--project={project_id}",
                    f"--transaction-file={tx_file}",
                ],
                log_output=False,
            )
            if tx_exec == 0:
                try:
                    os.remove(tx_file)
                except OSError:
                    pass
                return True

            self._abort_dns_transaction(parent_zone, project_id, tx_file)
            if not self._ns_delegation_exists(parent_zone, child_fqdn, project_id):
                return True
            self.logging.warning(
                f"[{cluster_name}] DNS NS remove execute failed "
                f"(attempt {attempt}/{max_retries}); retrying"
            )
            time.sleep(min(2 ** attempt, 16))

        self.logging.error(
            f"[{cluster_name}] Failed to remove NS delegation for {child_fqdn} "
            f"after {max_retries} attempts"
        )
        return False

    def _cleanup_cluster_dns(self, cluster_name, parent_domain, project_id):
        """Remove NS delegation from parent zone and delete child Cloud DNS zone."""
        if not parent_domain:
            self.logging.warning(f"[{cluster_name}] No base domain configured; skipping DNS cleanup")
            return
        parent_domain = self._normalize_dns_name(parent_domain)
        child_zone = cluster_name
        child_fqdn = f"{cluster_name}.{parent_domain}."

        parent_zone = self._find_parent_dns_zone(parent_domain, project_id)
        if parent_zone:
            list_code, list_out, _ = self.utils.subprocess_exec(
                f"gcloud dns record-sets list --zone={parent_zone} --project={project_id} "
                f"--name={child_fqdn} --type=NS --format=json",
                extra_params={"universal_newlines": True},
                log_output=False
            )
            records = []
            if list_code == 0 and list_out:
                try:
                    records = json.loads(list_out)
                except (ValueError, TypeError):
                    records = []
            if records:
                rrdatas = records[0].get("rrdatas", [])
                ttl = records[0].get("ttl", 300)
                if rrdatas:
                    self.logging.info(
                        f"[{cluster_name}] Removing NS delegation {child_fqdn} from {parent_zone}"
                    )
                    self._remove_ns_delegation(
                        cluster_name, parent_zone, child_fqdn, rrdatas, ttl, project_id
                    )

        describe_code, _, _ = self.utils.subprocess_exec(
            f"gcloud dns managed-zones describe {child_zone} --project={project_id}",
            log_output=False
        )
        if describe_code == 0:
            # Delete non-SOA/NS records so the managed zone can be removed
            rs_code, rs_out, _ = self.utils.subprocess_exec(
                f"gcloud dns record-sets list --zone={child_zone} --project={project_id} --format=json",
                extra_params={"universal_newlines": True},
                log_output=False
            )
            record_sets = []
            if rs_code == 0 and rs_out:
                try:
                    record_sets = json.loads(rs_out)
                except (ValueError, TypeError):
                    record_sets = []
            for rs in record_sets:
                rtype = rs.get("type", "")
                if rtype in ("SOA", "NS"):
                    continue
                name = rs.get("name", "")
                self.logging.info(f"[{cluster_name}] Deleting DNS record {rtype} {name}")
                self.utils.subprocess_exec(
                    f"gcloud dns record-sets delete {name} --type={rtype} "
                    f"--zone={child_zone} --project={project_id} --quiet",
                    log_output=False
                )
            self.logging.info(f"[{cluster_name}] Deleting Cloud DNS zone {child_zone}")
            self.utils.subprocess_exec(
                f"gcloud dns managed-zones delete {child_zone} --project={project_id} --quiet"
            )

    def create_cluster(self, platform, cluster_name):
        super().create_cluster(platform, cluster_name)
        myenv = self.gcp_process_env({"KUBECONFIG": self.environment["mc_kubeconfig"]})
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
        cluster_info["hostedclusters"] = self.environment["cluster_count"]
        cluster_info["install_method"] = "hypershiftcli"
        cluster_info["path"] = os.path.join(platform.environment["path"], cluster_name)
        os.makedirs(cluster_info["path"], exist_ok=True)

        project_id = self.environment["gcp_project_id"]
        region = self.environment["gcp_region"]
        ns = self.environment["hc_namespace"]
        cluster_path = cluster_info["path"]
        parent_domain = self.environment.get("base_domain", "").strip()

        self.logging.info(f"[{cluster_name}] Starting GCP HyperShift cluster creation")

        # Step 0: Create child Cloud DNS zone + NS delegation under parent base-domain
        self.logging.info(f"[{cluster_name}] Step 0: Creating Cloud DNS zone and parent NS delegation")
        dns_domain = self._ensure_cluster_dns(cluster_name, parent_domain, project_id)
        if not dns_domain:
            cluster_info["status"] = "Not Created"
            self.utils.increment_counter("clusters_created_failed")
            return 1
        cluster_info["parent_dns_domain"] = self._normalize_dns_name(parent_domain)
        cluster_info["dns_domain"] = dns_domain
        cluster_info["dns_zone"] = cluster_name
        self.logging.info(f"[{cluster_name}] Using child DNS domain {dns_domain} for hypershift create")

        # Step 1: Generate RSA keypair and JWKS
        self.logging.info(f"[{cluster_name}] Step 1: Generating RSA keypair and JWKS")
        key_path, jwks_path = self._generate_keys_and_jwks(cluster_path)

        # Force SA ADC on every GCP API call (do not fall back to user ADC).
        gcp_extra = {"env": myenv, "preexec_fn": self.utils.disable_signals}

        # Step 2: Create IAM resources
        self.logging.info(f"[{cluster_name}] Step 2: Creating IAM resources")
        iam_output = os.path.join(cluster_path, "iam-output.json")
        iam_code, _, _ = self.utils.subprocess_exec(
            f"hypershift create iam gcp --infra-id={cluster_name} --project-id={project_id} --oidc-jwks-file={jwks_path}",
            iam_output,
            gcp_extra,
        )
        if iam_code != 0:
            self.logging.error(f"[{cluster_name}] Failed to create IAM resources")
            cluster_info["status"] = "Not Created"
            self.utils.increment_counter("clusters_created_failed")
            return 1

        # Step 3: Create infrastructure
        self.logging.info(f"[{cluster_name}] Step 3: Creating infrastructure")
        infra_output = os.path.join(cluster_path, "infra-output.json")
        infra_code, _, _ = self.utils.subprocess_exec(
            f"hypershift create infra gcp --infra-id={cluster_name} --project-id={project_id} --region={region}",
            infra_output,
            gcp_extra,
        )
        if infra_code != 0:
            self.logging.error(f"[{cluster_name}] Failed to create infrastructure")
            cluster_info["status"] = "Not Created"
            self.utils.increment_counter("clusters_created_failed")
            return 1

        # Step 4: Parse IAM and infra outputs
        self.logging.info(f"[{cluster_name}] Step 4: Parsing IAM and infra outputs")
        try:
            iam = self._load_hypershift_json_output(
                iam_output, ("projectNumber", "workloadIdentityPool", "serviceAccounts")
            )
            infra = self._load_hypershift_json_output(
                infra_output, ("networkName", "subnetName")
            )
        except (ValueError, OSError) as err:
            self.logging.error(f"[{cluster_name}] Failed to parse IAM/infra output: {err}")
            cluster_info["status"] = "Not Created"
            self.utils.increment_counter("clusters_created_failed")
            return 1

        network_name = infra.get("networkName", "")
        subnet_name = infra.get("subnetName", "")
        project_number = iam.get("projectNumber", "")
        pool_id = iam.get("workloadIdentityPool", {}).get("poolId", "")
        provider_id = iam.get("workloadIdentityPool", {}).get("providerId", "")
        sa = iam.get("serviceAccounts", {})

        # Step 5: Create hosted cluster
        self.logging.info(f"[{cluster_name}] Step 5: Creating hosted cluster")
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())

        cluster_cmd = [
            "hypershift", "create", "cluster", "gcp",
            f"--name={cluster_name}",
            f"--namespace={ns}",
            f"--release-image={self.environment['release_image']}",
            f"--pull-secret={self.environment['pull_secret_path']}",
            f"--project={project_id}",
            f"--region={region}",
            f"--network={network_name}",
            f"--subnet={subnet_name}",
            f"--private-service-connect-subnet={subnet_name}",
            f"--endpoint-access={self.environment['endpoint_access']}",
            f"--workload-identity-project-number={project_number}",
            f"--workload-identity-pool-id={pool_id}",
            f"--workload-identity-provider-id={provider_id}",
            f"--control-plane-service-account={sa.get('ctrlplane-op', '')}",
            f"--node-pool-service-account={sa.get('nodepool-mgmt', '')}",
            f"--cloud-controller-service-account={sa.get('cloud-controller', '')}",
            f"--storage-service-account={sa.get('gcp-pd-csi', '')}",
            f"--image-registry-service-account={sa.get('image-registry', '')}",
            f"--network-service-account={sa.get('cloud-network', '')}",
            f"--service-account-signing-key-path={key_path}",
            f"--oidc-issuer-url=https://hypershift-{cluster_name}-oidc",
            f"--base-domain={dns_domain}",
            f"--external-dns-domain={dns_domain}",
            f"--node-pool-replicas={cluster_info['workers']}",
            f"--feature-set={self.environment['feature_set']}",
            f"--disable-cluster-capabilities={self.environment['disable_capabilities']}",
            '--annotations', 'hypershift.openshift.io/pod-security-admission-label-override=baseline',
        ]

        if platform.environment.get("wildcard_options"):
            for param in platform.environment["wildcard_options"].split():
                cluster_cmd.append(param)

        create_code, _, _ = self.utils.subprocess_exec(
            " ".join(cluster_cmd),
            os.path.join(cluster_path, "installation.log"),
            {"env": myenv, "preexec_fn": self.utils.disable_signals}
        )
        if create_code != 0:
            self.logging.error(f"[{cluster_name}] Cluster creation failed")
            cluster_info["status"] = "Not Created"
            self.utils.increment_counter("clusters_created_failed")
            return 1

        cluster_end_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["status"] = "Created"
        cluster_info["install_duration"] = cluster_end_time - cluster_start_time
        cluster_info["metadata"] = self.get_metadata(platform, cluster_name)
        self.logging.info(f"[{cluster_name}] Cluster created in {cluster_info['install_duration']}s")

        # Step 6: Wait for control plane
        self.logging.info(f"[{cluster_name}] Step 6: Waiting for control plane (10 min)")
        cluster_info["cluster_controlplane_ready"] = self.wait_for_controlplane_ready(cluster_name, 10)

        # Step 7: Download kubeconfig
        self.logging.info(f"[{cluster_name}] Step 7: Downloading kubeconfig")
        cluster_info["kubeconfig"] = self.download_kubeconfig(cluster_name, cluster_path)
        if not cluster_info["kubeconfig"]:
            self.logging.error(f"[{cluster_name}] Failed to download kubeconfig")
            cluster_info["workers_wait_time"] = None
            cluster_info["status"] = "Completed. Not Access"
            self.utils.increment_counter("clusters_created_failed")
            return 1

        # Step 8: Wait for workers
        if cluster_info.get("workers_wait_time"):
            cluster_info["workers_ready"] = self._wait_for_workers(
                cluster_info["kubeconfig"], cluster_info["workers"],
                cluster_info["workers_wait_time"], cluster_name
            )

        # Step 9: Wait for cluster Completed
        self.logging.info(f"[{cluster_name}] Step 9: Waiting for Completed status (60 min)")
        cluster_info["cluster_ready"] = self.wait_for_cluster_ready(cluster_name, 60)
        cluster_info["status"] = "Completed"

        # Write metadata
        with open(os.path.join(cluster_path, "metadata_install.json"), "w") as f:
            json.dump(cluster_info, f, indent=2)

        if self.es is not None:
            self.logging.info(f"[{cluster_name}] Indexing install metadata to Elasticsearch")
            self.es.index_metadata(cluster_info)
            os.environ["START_TIME"] = str(cluster_start_time)
            os.environ["END_TIME"] = str(cluster_end_time)
            self.logging.info(
                f"[{cluster_name}] Waiting 120s then indexing cluster metrics via e2e-benchmarking"
            )
            time.sleep(120)
            self.utils.cluster_load(platform, cluster_name, load="index")
        else:
            self.logging.warning(
                f"[{cluster_name}] ES is not configured (pass --es-url / HCP_BURNER_ES_URL); "
                f"skipping install metadata and e2e-benchmarking index. "
                f"Timings saved locally in {cluster_path}/metadata_install.json"
            )

        self.utils.increment_counter("clusters_created_success")
        return 0

    def delete_cluster(self, platform, cluster_name):
        # Mirrors https://github.com/Sandeepyadav93/gcp-selfhosted/blob/main/step5_delete_hc_cluster.sh
        super().delete_cluster(platform, cluster_name)
        myenv = self.gcp_process_env({"KUBECONFIG": self.environment["mc_kubeconfig"]})
        cluster_info = platform.environment["clusters"][cluster_name]
        cluster_info["uuid"] = self.environment["uuid"]
        cluster_info["timestamp"] = datetime.datetime.utcnow().isoformat()
        cluster_info["install_method"] = "hypershiftcli"

        project_id = self.environment["gcp_project_id"]
        region = self.environment["gcp_region"]
        ns = self.environment["hc_namespace"]
        cluster_path = cluster_info.get("path", os.path.join(platform.environment["path"], cluster_name))
        os.makedirs(cluster_path, exist_ok=True)
        extra = {"env": myenv, "preexec_fn": self.utils.disable_signals}

        self.logging.info(f"[{cluster_name}] Deleting GCP HyperShift cluster")
        cluster_start_time = int(datetime.datetime.utcnow().timestamp())

        # Step 1: Destroy hosted cluster (continue on failure, like the bash script)
        self.logging.info(f"[{cluster_name}] Step 1: Destroying hosted cluster")
        destroy_code, _, _ = self.utils.subprocess_exec(
            f"hypershift destroy cluster gcp --name={cluster_name} --namespace={ns}",
            os.path.join(cluster_path, "cleanup-cluster.log"),
            extra,
            log_output=False
        )
        if destroy_code != 0:
            self.logging.warning(
                f"[{cluster_name}] Hosted cluster deletion failed or cluster not found "
                f"(exit {destroy_code}); continuing with infra and IAM cleanup"
            )

        # Step 2: Destroy infrastructure
        self.logging.info(f"[{cluster_name}] Step 2: Destroying infrastructure")
        infra_code, _, _ = self.utils.subprocess_exec(
            f"hypershift destroy infra gcp --infra-id={cluster_name} --project-id={project_id} --region={region}",
            os.path.join(cluster_path, "cleanup-infra.log"),
            extra
        )

        # Step 3: Destroy IAM resources
        self.logging.info(f"[{cluster_name}] Step 3: Destroying IAM resources")
        iam_code, _, _ = self.utils.subprocess_exec(
            f"hypershift destroy iam gcp --infra-id={cluster_name} --project-id={project_id}",
            os.path.join(cluster_path, "cleanup-iam.log"),
            extra
        )

        # Step 4: Remove NS delegation and delete child Cloud DNS zone
        self.logging.info(f"[{cluster_name}] Step 4: Cleaning up Cloud DNS zone and NS delegation")
        parent_domain = (
            cluster_info.get("parent_dns_domain")
            or self.environment.get("base_domain", "")
        )
        self._cleanup_cluster_dns(cluster_name, parent_domain, project_id)

        cluster_end_time = int(datetime.datetime.utcnow().timestamp())
        cluster_info["destroy_duration"] = cluster_end_time - cluster_start_time

        if infra_code == 0 and iam_code == 0:
            cluster_info["status"] = "deleted"
            self.utils.increment_counter("clusters_deleted_success")
            self.logging.info(f"[{cluster_name}] Cluster deletion complete")
        else:
            cluster_info["status"] = "not deleted"
            self.utils.increment_counter("clusters_deleted_failed")
            self.logging.error(
                f"[{cluster_name}] Cleanup incomplete (infra exit={infra_code}, iam exit={iam_code})"
            )

        with open(os.path.join(cluster_path, "metadata_destroy.json"), "w") as f:
            json.dump(cluster_info, f, indent=2)

        if self.es is not None:
            self.es.index_metadata(cluster_info)
        return 0

    def get_workers_ready(self, kubeconfig, cluster_name):
        super().get_workers_ready(kubeconfig, cluster_name)
        myenv = os.environ.copy()
        myenv["KUBECONFIG"] = kubeconfig
        code, out, _ = self.utils.subprocess_exec(
            "oc get nodes -o json",
            extra_params={"env": myenv, "universal_newlines": True},
            log_output=False
        )
        try:
            nodes = json.loads(out).get("items", [])
        except (ValueError, TypeError):
            return 0
        return sum(
            1 for node in nodes
            if any(c.get("type") == "Ready" and c.get("status") == "True"
                   for c in node.get("status", {}).get("conditions", []))
        )


class HypershiftcliArguments(GcpArguments):
    def __init__(self, parser, config_file, environment):
        super().__init__(parser, config_file, environment)
        EnvDefault = self.EnvDefault

        parser.add_argument("--mc-kubeconfig", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_MC_KUBECONFIG", help="Kubeconfig file for the MC (management) cluster")
        parser.add_argument("--release-image", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_RELEASE_IMAGE", help="OpenShift release image")
        parser.add_argument("--pull-secret-path", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_PULL_SECRET_PATH", help="Path to pull secret file")
        parser.add_argument("--base-domain", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_BASE_DOMAIN", default="", help="Parent DNS domain (e.g. gcp.hyp.azure.rhperfscale.org). A child zone <cluster-name>.<base-domain> is always created and NS-delegated for hypershift --base-domain/--external-dns-domain")
        parser.add_argument("--hc-namespace", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_HC_NAMESPACE", default="clusters", help="Hosted cluster namespace on MC")
        parser.add_argument("--feature-set", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_FEATURE_SET", default="TechPreviewNoUpgrade", help="Feature set for the cluster")
        parser.add_argument("--endpoint-access", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_ENDPOINT_ACCESS", default="PublicAndPrivate", help="Endpoint access type")
        parser.add_argument("--disable-capabilities", action=EnvDefault, env=environment, envvar="HCP_BURNER_GCP_DISABLE_CAPABILITIES", default="Console,Ingress", help="Comma-separated cluster capabilities to disable")

        if config_file:
            config = configparser.ConfigParser()
            config.read(config_file)
            defaults = {}
            defaults.update(dict(config.items("Platform:Gcp:Hypershiftcli")))
            parser.set_defaults(**defaults)

        temp_args, _ = parser.parse_known_args()
        if not temp_args.mc_kubeconfig or not temp_args.release_image or not temp_args.pull_secret_path or not temp_args.base_domain:
            parser.error("hcp-burner.py: error: the following arguments (or equivalent definition) are required: --mc-kubeconfig, --release-image, --pull-secret-path, --base-domain")
