# Platforms

Each platform must contain two classes, one to define the methods of the platform, and another one to define the arguments of the platform.

Parent Platform (Platform) defines the methods and variables used by all the platform.
This is the platform where to define ocm related commands, like `ocm login` because (at this moment) it is being used by all the child platforms.

## Platforms Arguments

To use the config file, define parameters related to platform under the `[Platform]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --ocm-token              |   | ocm_token                     | ROSA_BURNER_OCM_TOKEN          |
| --ocm-url                | https://api.stage.openshift.com | ocm_url | ROSA_BURNER_OCM_URL                     |
