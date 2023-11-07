FROM python:3.11 as runtime
USER root
RUN curl -L https://go.dev/dl/go1.18.2.linux-amd64.tar.gz -o go1.18.2.linux-amd64.tar.gz
RUN tar -C /usr/local -xzf go1.18.2.linux-amd64.tar.gz
ENV PATH=$PATH:/usr/local/go/bin
RUN python3 -m pip install --upgrade pip || true
RUN yes | pip3 install openshift --upgrade || true
RUN apt-get -y update
RUN apt-get -y install jq
RUN curl -L $(curl -s https://api.github.com/repos/openshift/rosa/releases/latest | jq -r ".assets[] | select(.name == \"rosa-linux-amd64\") | .browser_download_url") --output /usr/local/bin/rosa
RUN curl -L $(curl -s https://api.github.com/repos/openshift-online/ocm-cli/releases/latest | jq -r ".assets[] | select(.name == \"ocm-linux-amd64\") | .browser_download_url") --output /usr/local/bin/ocm
RUN chmod +x /usr/local/bin/rosa && chmod +x /usr/local/bin/ocm
RUN /usr/local/bin/rosa download openshift-client
RUN tar xzvf openshift-client-linux.tar.gz
RUN mv oc kubectl /usr/local/bin/
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
RUN unzip awscliv2.zip
RUN ./aws/install
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash
COPY . /
