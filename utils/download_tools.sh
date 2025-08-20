#!/usr/bin/env bash
# Tool for downloading required binaries used by the hcp-burner script
# ocm: tool for managing managed clusters
# rosa: tool for managing rosa clusters
# terraform: tool for creating resources on AWS
DESTINATION_FOLDER="${1:-/usr/local/bin}"
mkdir -p "${DESTINATION_FOLDER}"

download_ocm(){
  curl -L -o "${DESTINATION_FOLDER}/ocm" "$(curl -s https://api.github.com/repos/openshift-online/ocm-cli/releases/latest | jq -r '.assets | map(select(.name | contains("ocm-linux-amd64"))) | .[0].browser_download_url')"
  chmod a+x "${DESTINATION_FOLDER}/ocm"
}

download_rosa(){
  echo "Finding the latest ROSA CLI release..."
  # 1. Find the download URL for the latest Linux tar.gz file
  DOWNLOAD_URL=$(curl -s https://api.github.com/repos/openshift/rosa/releases/latest | jq -r '.assets | map(select(.name | endswith("Linux_x86_64.tar.gz"))) | .[0].browser_download_url')

  echo "Downloading from ${DOWNLOAD_URL}"
  # 2. Download the archive to the current directory
  curl -L -o rosa-latest.tar.gz "${DOWNLOAD_URL}"

  echo "Extracting the 'rosa' binary..."
  # 3. Extract just the 'rosa' binary from the archive
  tar -xzf rosa-latest.tar.gz rosa

  echo "Installing to ${DESTINATION_FOLDER}..."
  # 4. Move the binary to its final destination
  mv rosa "${DESTINATION_FOLDER}/rosa"

  # 5. Make the binary executable
  chmod a+x "${DESTINATION_FOLDER}/rosa"

  echo "Cleaning up..."
  # 6. Remove the downloaded archive
  rm rosa-latest.tar.gz

  echo "ROSA CLI installed successfully!"
}

download_terraform(){
  curl -L -o "${DESTINATION_FOLDER}/terraform.zip" https://releases.hashicorp.com/terraform/1.5.3/terraform_1.5.3_linux_amd64.zip
  pushd "${DESTINATION_FOLDER}" || return 1
  unzip -fo terraform.zip
  popd || return 1
}

download_awscli(){
  curl -L -o "${DESTINATION_FOLDER}/awscli.zip" https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip
  pushd "${DESTINATION_FOLDER}" || return 1
  unzip -fo awscli.zip
  popd || return 1
}
  
download_ocm
download_rosa
download_terraform
download_awscli
