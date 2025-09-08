#!/usr/bin/env bash
#set -e
set -o pipefail

# Function to capture SC/MC data
capture_cluster_data() {
  echo "####################################################"
  echo "$(date -u) :: Collecting Data for $(basename "${2}")"
  mkdir -p "${2}"

  echo "$(date -u) :: Collecting clusterversion data"
  oc --kubeconfig "${1}" get clusterversion > "${2}/clusterversion"
  oc --kubeconfig "${1}" get clusterversion -o yaml > "${2}/clusterversion.yaml"
  oc --kubeconfig "${1}" describe clusterversion > "${2}/clusterversion.describe"

  echo "$(date -u) :: Collecting clusteroperator data"
  oc --kubeconfig "${1}" get clusteroperators > "${2}/clusteroperators"
  oc --kubeconfig "${1}" get clusteroperators -o yaml > "${2}/clusteroperators.yaml"
  oc --kubeconfig "${1}" describe clusteroperators > "${2}/clusteroperators.describe"

  oc --kubeconfig "${1}" get cm -n kube-system cluster-config-v1 -o yaml > "${2}/cluster-config-v1"

  echo "$(date -u) :: Collecting csv data"
  oc --kubeconfig "${1}" get csv -A > "${2}/csv"

  echo "$(date -u) :: Collecting node data"
  oc --kubeconfig "${1}" get no > "${2}/nodes"
  oc --kubeconfig "${1}" get no -o yaml > "${2}/nodes.yaml"
  oc --kubeconfig "${1}" describe no > "${2}/nodes.describe"

  echo "$(date -u) :: Collecting namespace data"
  oc --kubeconfig "${1}" get ns > "${2}/namespaces"
  oc --kubeconfig "${1}" get ns -o yaml > "${2}/namespaces.yaml"

  echo "$(date -u) :: Collecting KAS pod data"
  # Get KAS Pods Only
  oc --kubeconfig "${1}" get po -n openshift-kube-apiserver -l apiserver=true -o wide > "${2}/pods.kas"
  oc --kubeconfig "${1}" get po -n openshift-kube-apiserver -l apiserver=true -o yaml > "${2}/pods.kas.yaml"
  oc --kubeconfig "${1}" describe po -n openshift-kube-apiserver -l apiserver=true > "${2}/pods.kas.describe"

  echo "$(date -u) :: Collecting Etcd pod data"
  # Get Etcd Pods Only
  oc --kubeconfig "${1}" get po -n openshift-etcd -l app=etcd -o wide > "${2}/pods.etcd"
  oc --kubeconfig "${1}" get po -n openshift-etcd -l app=etcd -o yaml > "${2}/pods.etcd.yaml"
  oc --kubeconfig "${1}" describe po -n openshift-etcd -l app=etcd > "${2}/pods.etcd.describe"

  echo "$(date -u) :: Collecting All pod data"
  # Get all Pods
  oc --kubeconfig "${1}" get pods -A -o wide > "${2}/pods"
  oc --kubeconfig "${1}" get pods -A -o yaml > "${2}/pods.yaml"
  # oc --kubeconfig "${1}" describe pods -A > "${2}/pods.describe"

  # echo "$(date -u) :: Collecting event data"
  # oc --kubeconfig "${1}" get ev -A > "${2}/events"
  # oc --kubeconfig "${1}" get ev -A -o yaml > "${2}/events.yaml"
}

capture_mc_data() {
  echo "$(date -u) :: Collecting Management Cluster Data for $(basename "${2}")"

  echo "$(date -u) :: Collecting HCP"
  oc --kubeconfig "${1}" get hcp -A > "${2}/hcp"
  oc --kubeconfig "${1}" get hcp -A -o yaml > "${2}/hcp.yaml"
  # oc --kubeconfig "${1}" describe hcp -A > ${2}/hcp.describe

  echo "$(date -u) :: Collecting HC Data"
  oc --kubeconfig "${1}" get hc -A > "${2}/hc"
  oc --kubeconfig "${1}" get hc -A -o yaml > "${2}/hc.yaml"
  # oc --kubeconfig "${1}" describe hc -A > "${2}/hc.describe"

  echo "$(date -u) :: Collecting machineset.machine Data"
  oc --kubeconfig "${1}" get machineset.machine -A > "${2}/machineset.machine"
}

capture_sc_data() {
  echo "$(date -u) :: Collecting Service Cluster Data for $(basename "${2}")"

  echo "$(date -u) :: Collecting MCH Data"
  oc --kubeconfig "${1}" get mch -A > "${2}/mch"
  oc --kubeconfig "${1}" get mch -A -o yaml > "${2}/mch.yaml"
  oc --kubeconfig "${1}" describe mch -A > "${2}/mch.describe"

  echo "$(date -u) :: Collecting MCE Data"
  oc --kubeconfig "${1}" get mce > "${2}/mce"
  oc --kubeconfig "${1}" get mce -o yaml > "${2}/mce.yaml"
  oc --kubeconfig "${1}" describe mce > "${2}/mce.describe"

  echo "$(date -u) :: Collecting Managedcluster Data"
  oc --kubeconfig "${1}" get managedcluster > "${2}/managedcluster"
  oc --kubeconfig "${1}" get managedcluster -o yaml > "${2}/managedcluster.yaml"
}

ts="$(date -u +%Y%m%d)"
iteration=${ts}-0

output_dir=${iteration}
mkdir -p "${output_dir}"

if [ ! -d "$1" ]; then
  echo "Error: '$1' is not a valid directory."
  exit 1
fi

kcs=($(ls "${1}" | grep kubeconfig))

for item in "${kcs[@]}"; do
  if [[ "$item" =~ "sc" ]]; then
    sc_output_dir=${output_dir}/${item}
    capture_cluster_data "${1}/${item}" "${sc_output_dir}"
    capture_sc_data "${1}/${item}" "${sc_output_dir}"
  elif [[ "$item" =~ "mc" ]]; then
    mc_output_dir=${output_dir}/${item}
    capture_cluster_data "${1}/${item}" "${mc_output_dir}"
    capture_mc_data "${1}/${item}" "${mc_output_dir}"
  fi
done

# Collect must-gathers and compress them
for item in "${kcs[@]}"; do
  echo "####################################################"
  echo "$(date -u) :: Collecting must-gather for ${item}"
  oc --kubeconfig "${1}/${item}" adm must-gather --dest-dir "${output_dir}/must-gather-${item}" > "${output_dir}/must-gather-${item}.log"
  tar caf "${output_dir}/must-gather-${item}.tar.gz" --remove-files "${output_dir}/must-gather-${item}"
done

echo "####################################################"
echo "$(date -u) :: Done collecting data"
