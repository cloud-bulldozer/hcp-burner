FROM python:3.11 as runtime
USER root
RUN curl -L https://go.dev/dl/go1.18.2.linux-amd64.tar.gz -o go1.18.2.linux-amd64.tar.gz
RUN tar -C /usr/local -xzf go1.18.2.linux-amd64.tar.gz
ENV PATH=$PATH:/usr/local/go/bin
RUN python3 -m pip install --upgrade pip || true
RUN yes | pip3 install openshift elasticsearch==7.13.4 gitpython packaging --upgrade || true
RUN apt-get -y update
RUN apt-get -y install jq groff uuid-runtime
RUN curl -L $(curl -s https://api.github.com/repos/openshift/rosa/releases/latest | jq -r ".assets[] | select(.name == \"rosa-linux-amd64\") | .browser_download_url") --output /usr/local/bin/rosa
RUN curl -L $(curl -s https://api.github.com/repos/openshift-online/ocm-cli/releases/latest | jq -r ".assets[] | select(.name == \"ocm-linux-amd64\") | .browser_download_url") --output /usr/local/bin/ocm
RUN curl -L https://releases.hashicorp.com/terraform/1.8.0/terraform_1.8.0_linux_amd64.zip -o terraform_1.8.0_linux_amd64.zip
RUN unzip terraform_1.8.0_linux_amd64.zip -d /usr/local/bin/
RUN chmod +x /usr/local/bin/rosa && chmod +x /usr/local/bin/ocm && chmod +x /usr/local/bin/terraform
RUN /usr/local/bin/rosa download openshift-client
RUN tar xzvf openshift-client-linux.tar.gz
RUN mv oc kubectl /usr/local/bin/
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
RUN unzip awscliv2.zip
RUN ./aws/install
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash
RUN curl --fail --retry 8 --retry-all-errors -sS -L "https://github.com/kube-burner/kube-burner/releases/download/v1.9.5/kube-burner-V1.9.5-linux-x86_64.tar.gz" | tar -xzC "/usr/local/bin/" kube-burner
COPY . /
