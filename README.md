# F5xc UDF Lab Services

**These are unusable outside of UDF -- they rely on the UDF metadata service.**

## tops-lab
This is the base service for all UDF labs.

### Actions performed
- [X] Pulls Deployment info from the UDF metadata service
- [X] Pulls Lab info from a f5xc-tenantOps S3 bucket
- [X] Creates a petname
- [X] Writes a state file for continuity between deployment start/stops
- [X] Sends an SQS message to kick off account and resource provisioning in F5XC
- [X] Continues sending SQSs to signify the deployment is active

### Requirements
The UDF deployment's "runner" instance must be must be tagged:

<img src="./images/tags.png" alt="tags" width="512"/>

### Installation
Run the [installer](./lab/tops_lab_install.sh) on the "runner" instance.


