# Introduction #

This is a short document describing an example configuration to enable detailed monitoring of a ScoutAM system using StatsD for time series data, Prometheus as the data store, and Grafana as the visualization component of the solution. Out of scope for this document are securing the infrastructure beyond what is already provided with ScoutAM, retaining and protecting time series data in Prometheus or any other time series data store, and securing Grafana.

The main components and flow of data are shown in the following figure:

                          +---------+
                          | Browser |
                          +---------+
                               ^
                               |
                       HTTP/API Requests
                               |
                          +---------+
                          | Grafana |
                          +---------+
                               ^
                               |
                         PromQL Queries
                               |
                 +---------------------------+
                 |    Prometheus Service     |
                 +---------------------------+
                               ^
                               |
         +---------------------+-----------------------+
         |                     |                       |
    Metrics Scrape        Metrics Scrape          Metrics Scrape
         |                     |                       |
   +-------------+       +-------------+        +-------------+
   | StatsD/Node |       | StatsD/Node |        | StatsD/Node |
   |   Exporter  |       |   Exporter  |        |   Exporter  |
   +-------------+       +-------------+        +-------------+
         |                     |                       |
   +-------------+       +-------------+        +-------------+
   |   ScoutAM   |       |   ScoutAM   |        |   ScoutAM   |
   |   Service   |       |   Service   |        |   Service   |
   +-------------+       +-------------+        +-------------+
         |                     |                       |
   +-------------+       +-------------+        +-------------+
   |   ScoutAM   |       |   ScoutAM   |        |   ScoutAM   |
   |     Node    |       |     Node    |        |    Node     |
   +-------------+       +-------------+        +-------------+

In the above figure, each ScoutAM node running the ScoutAM service is configured to export StatsD metrics to the local node, which also runs the StatsD Exporter provided by Prometheus. Additionally, each node runs the Node Exporter, which Prometheus supplies to export system metrics for components such as network, local disk, CPU, memory, etc.

Statistics from StatsD and Node Exporters are periodically scraped from Prometheus and stored. Finally, Grafana provides a visual display of the metrics.

Leveraging Grana enables data visualization over more extended periods and in more detail than is available with the ScoutAM GUI.

# Prometheus Configuration #

The following example `prometheus.yml` file has defaults for everything except for the jobs `statsd_exporter` and `node_exporter`, which list the ScoutAM nodes in the configuration.

```YAML
global:
  scrape_interval: 15s
  evaluation_interval: 15s


alerting:
  alertmanagers:
    - static_configs:
        - targets:


rule_files:


scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["admin.local:9090"]


  - job_name: "statsd_exporter"
    static_configs:
      - targets: ["s1.local:9102", "s2.local:9102", "s3.local:9102"]


  - job_name: "node_exporter"
    static_configs:
      - targets: ["s1.local:9100", "s2.local:9100", "s3.local:9100"]
```

# StatsD Exporter Configuration #

In this example, the default configuration for the StatsD exporter was used. A SystemD service was created on each ScoutAM node with the following:

```
[Unit]
Description=Prometheus StatsD Exporter
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=1
User=root
ExecStart=/mnt/admin/software/prometheus/statsd_exporter/statsd_exporter

[Install]
WantedBy=multi-user.target

Node Exporter Configuration
The standard Node Exporter configuration can be used. An example SystemD service:

[Unit]
Description=Prometheus node exporter
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=1
User=root
ExecStart=/usr/local/bin/node_exporter

[Install]
WantedBy=multi-user.target
```

The Node Exporter can also be configured to create custom statistics using metrics files that end in `.prom` file extension. For example, by default, ScoutAM does not emit StatsD metrics for accounting data. If you wish to include accounting data in the metrics and visualize it in Grafana, those metrics can be collected from the system and stored in a `.prom` file.

In the following example `.prom` file, cache accounting metrics, the states of the ScoutAM scheduler, and a stat for the leader are posted. 

```
scoutam_acct{name="noarchive", type="cache", metric="files"} 0
scoutam_acct{name="unmatched", type="cache", metric="files"} 0
scoutam_acct{name="damaged", type="cache", metric="files"} 0
scoutam_scheduler{queue="SCHEDULER", idle="False"} 1
scoutam_scheduler{queue="ARCHIVING", idle="True"} 0
scoutam_scheduler{queue="STAGING", idle="False"} 1
scoutam_leader{fqdn="s81.local", hostname="s81", leader="True"} 1
```

The script that generates the output is `scoutam_node_exporter.py`.

To execute the script, you can create a cron job that runs every minute:

```
# Example of job definition:
# .---------------- minute (0 - 59)
# |  .------------- hour (0 - 23)
# |  |  .---------- day of month (1 - 31)
# |  |  |  .------- month (1 - 12) OR jan,feb,mar,apr ...
# |  |  |  |  .---- day of week (0 - 6) (Sunday=0 or 7) OR sun,mon,tue,wed,thu,fri,sat
# |  |  |  |  |
# *  *  *  *  * user-name  command to be executed
  *  *  *  *  * root scoutam_node_exporter.py --file /var/tmp/prometheus/node_exporter/acct.prom
```

To collect the metrics from the file that the script generates, you can add the `--collector.textfile.directory` argument to the `node_exporter` command. Here, the argument has been added to the SystemD service:

```
[Unit]
Description=Prometheus node exporter
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=1
User=root
ExecStart=/usr/local/bin/node_exporter --collector.textfile.directory=/var/tmp/prometheus/node_exporter

[Install]
WantedBy=multi-user.target
```
