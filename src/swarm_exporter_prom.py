#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2023 NeuroForge GmbH & Co. KG <https://neuroforge.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from datetime import datetime, timedelta
import docker
from prometheus_client import start_http_server, Counter, Gauge
import os
import platform
from typing import Any, Optional
import traceback
from threading import Event
import signal

exit_event = Event()

shutdown: bool = False
def handle_shutdown(signal: Any, frame: Any) -> None:
    print_timed(f"received signal {signal}. shutting down...")
    exit_event.set()

signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


APP_NAME = "Docker Swarm prometheus exporter"

PROMETHEUS_EXPORT_PORT = int(os.getenv('PROMETHEUS_EXPORT_PORT', '9456'))
DOCKER_HOSTNAME = os.getenv('DOCKER_HOSTNAME', platform.node())
SCRAPE_INTERVAL = int(os.getenv('SCRAPE_INTERVAL', '10'))
MAX_RETRIES_IN_ROW = int(os.getenv('MAX_RETRIES_IN_ROW', '10'))

DOCKER_SWARM_NODE = Counter(
    'docker_swarm_node',
    'Docker Swarm node information',
    [
        'docker_swarm_node_id',
        'docker_swarm_node_spec_role',
        'docker_swarm_node_spec_availability',
        'docker_swarm_node_description_hostname',
        'docker_swarm_node_description_platform_architecture',
        'docker_swarm_node_description_platform_os',
        'docker_swarm_node_description_engine_engineversion',
        'docker_swarm_node_status_state',
        'docker_swarm_node_status_addr',
        'docker_swarm_node_managerstatus_leader',
        'docker_swarm_node_managerstatus_reachability',
        'docker_swarm_node_managerstatus_addr',
    ]
)

DOCKER_SWARM_SERVICE = Gauge(
    'docker_swarm_service',
    'Docker Swarm service information',
    [
        'docker_swarm_service_id',
        'docker_swarm_service_name',
        'docker_swarm_service_version',
        'docker_swarm_service_created_at',
        'docker_swarm_service_updated_at',
        'docker_swarm_service_mode',
        'docker_swarm_service_replicas',
        'docker_swarm_service_image',
    ]
)

DOCKER_SWARM_TASK = Gauge(
    'docker_swarm_task',
    'Docker Swarm task information',
    [
        'docker_swarm_task_id',
        'docker_swarm_task_name',
        'docker_swarm_task_service_id',
        'docker_swarm_task_service_name',
        'docker_swarm_task_node_id',
        'docker_swarm_task_state',
        'docker_swarm_task_desired_state',
        'docker_swarm_task_created_at',
        'docker_swarm_task_updated_at',
        'docker_swarm_task_image',
    ]
)

def print_timed(msg):
    to_print = '{} [{}]: {}'.format(
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'docker_events',
        msg)
    print(to_print)


def watch_swarm():
    client = docker.DockerClient()
    # Create an APIClient for low-level API access
    api_client = docker.APIClient()

    try:
        while not exit_event.is_set():
            # Reset all gauges to avoid stale metrics
            DOCKER_SWARM_SERVICE._metrics.clear()
            DOCKER_SWARM_TASK._metrics.clear()

            # Collect node metrics
            nodes = client.nodes.list()
            for node in nodes:
                attrs = node.attrs
                DOCKER_SWARM_NODE.labels(
                    **{
                        'docker_swarm_node_id': attrs['ID'],
                        'docker_swarm_node_spec_role': attrs['Spec'].get('Role', ''),
                        'docker_swarm_node_spec_availability': attrs['Spec'].get('Availability', ''),
                        'docker_swarm_node_description_hostname': attrs.get('Description', {}).get('Hostname', ''),
                        'docker_swarm_node_description_platform_architecture':  attrs.get('Description', {}).get('Platform', {}).get('OS', 'Architecture'),
                        'docker_swarm_node_description_platform_os': attrs.get('Description', {}).get('Platform', {}).get('OS', ''),
                        'docker_swarm_node_description_engine_engineversion': attrs.get('Description', {}).get('Engine', {}).get('EngineVersion', ''),
                        'docker_swarm_node_status_state': attrs.get('Status', {}).get('State', ''),
                        'docker_swarm_node_status_addr': attrs.get('Status', {}).get('Addr', ''),
                        'docker_swarm_node_managerstatus_leader': attrs.get('ManagerStatus', {}).get('Leader', False),
                        'docker_swarm_node_managerstatus_reachability': attrs.get('ManagerStatus', {}).get('Reachability', ''),
                        'docker_swarm_node_managerstatus_addr': attrs.get('ManagerStatus', {}).get('Addr', ''),
                    }).inc()

            # Collect service metrics
            services = client.services.list()
            for service in services:
                attrs = service.attrs
                spec = attrs.get('Spec', {})
                task_template = spec.get('TaskTemplate', {})
                container_spec = task_template.get('ContainerSpec', {})
                mode = spec.get('Mode', {})
                replicated = mode.get('Replicated', {})
                replicas = replicated.get('Replicas', 0) if replicated else 0

                # If service is in global mode, set replicas to -1 to indicate global
                if 'Global' in mode:
                    replicas = -1

                DOCKER_SWARM_SERVICE.labels(
                    docker_swarm_service_id=attrs.get('ID', ''),
                    docker_swarm_service_name=spec.get('Name', ''),
                    docker_swarm_service_version=str(attrs.get('Version', {}).get('Index', '')),
                    docker_swarm_service_created_at=attrs.get('CreatedAt', ''),
                    docker_swarm_service_updated_at=attrs.get('UpdatedAt', ''),
                    docker_swarm_service_mode='global' if 'Global' in mode else 'replicated',
                    docker_swarm_service_replicas=str(replicas),
                    docker_swarm_service_image=container_spec.get('Image', ''),
                ).set(1)  # Set to 1 to indicate the service exists

            # Collect task metrics
            tasks = api_client.tasks()
            for task in tasks:
                # With APIClient, task is already a dictionary

                # Get service name from service ID
                service_id = task.get('ServiceID', '')
                service_name = ''
                for service in services:
                    if service.id == service_id:
                        service_name = service.attrs.get('Spec', {}).get('Name', '')
                        break

                DOCKER_SWARM_TASK.labels(
                    docker_swarm_task_id=task.get('ID', ''),
                    docker_swarm_task_name=task.get('Name', ''),
                    docker_swarm_task_service_id=service_id,
                    docker_swarm_task_service_name=service_name,
                    docker_swarm_task_node_id=task.get('NodeID', ''),
                    docker_swarm_task_state=task.get('Status', {}).get('State', ''),
                    docker_swarm_task_desired_state=task.get('DesiredState', ''),
                    docker_swarm_task_created_at=task.get('CreatedAt', ''),
                    docker_swarm_task_updated_at=task.get('UpdatedAt', ''),
                    docker_swarm_task_image=task.get('Spec', {}).get('ContainerSpec', {}).get('Image', ''),
                ).set(1)  # Set to 1 to indicate the task exists

            exit_event.wait(SCRAPE_INTERVAL)
    finally:
        client.close()
        api_client.close()


if __name__ == '__main__':
    print_timed(f'Start prometheus client on port {PROMETHEUS_EXPORT_PORT}')
    start_http_server(PROMETHEUS_EXPORT_PORT, addr='0.0.0.0')

    failure_count = 0
    last_failure: Optional[datetime] = None
    while not exit_event.is_set():
        try:
            print_timed('Watch Docker Swarm')
            watch_swarm()
        except docker.errors.APIError:
            now = datetime.now()
            traceback.print_exc()

            if last_failure is not None and last_failure < (now - timedelta.seconds(SCRAPE_INTERVAL * 10)):
                print_timed("detected docker APIError, but last error was a bit back, resetting failure count.")
                # last failure was a while back, reset
                failure_count = 0

            failure_count += 1
            if failure_count > MAX_RETRIES_IN_ROW:
                print_timed(f"failed {failure_count} in a row. exit_eventing...")
                exit(1)

            last_failure = now
            print_timed(f"waiting {SCRAPE_INTERVAL} until next cycle")
            exit_event.wait(SCRAPE_INTERVAL)
