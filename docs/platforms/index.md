# Platforms

Each platform must contain two classes, one to define the methods of the platform, and another one to define the arguments.

Parent Platform (Platform) defines the methods and variables used by all the platforms.
This is the platform where to define, for example, ocm related commands, like `ocm login` because (at this moment) it is being used by all the child platforms.

## Platforms Arguments

To use the config file, define parameters related to platform under the `[Platform]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --ocm-token              |   | ocm_token                     | HCP_BURNER_OCM_TOKEN          |
| --ocm-url                | https://api.stage.openshift.com | ocm_url | HCP_BURNER_OCM_URL                     |
