"""
 This file is part of nucypher.

 nucypher is free software: you can redistribute it and/or modify
 it under the terms of the GNU Affero General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 nucypher is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU Affero General Public License for more details.

 You should have received a copy of the GNU Affero General Public License
 along with nucypher.  If not, see <https://www.gnu.org/licenses/>.
"""
from nucypher.utilities.prometheus.collector import MetricsCollector, UrsulaInfoMetricsCollector, \
    BlockchainMetricsCollector, StakerMetricsCollector, WorkerMetricsCollector, WorkLockMetricsCollector, \
    EventMetricsCollector, ReStakeEventMetricsCollector, WindDownEventMetricsCollector, \
    WorkerBondedEventMetricsCollector, RefundEventMetricsCollector, BidEventMetricsCollector

try:
    from prometheus_client import Gauge, Enum, Counter, Info, Histogram, Summary
except ImportError:
    raise ImportError('prometheus_client is not installed - Install it and try again.')

from nucypher.characters.lawful import Ursula
import json
from typing import List, Dict

from prometheus_client.core import Timestamp
from prometheus_client.registry import REGISTRY
from prometheus_client.utils import floatToGoString
from twisted.internet import reactor, task
from twisted.web.resource import Resource

from nucypher.blockchain.eth.agents import ContractAgency, StakingEscrowAgent, WorkLockAgent, PolicyManagerAgent


class PrometheusMetricsConfig:
    def __init__(self, port: int, metrics_prefix: str, listen_address: str):
        self.port = port
        self.metrics_prefix = metrics_prefix
        self.listen_address = listen_address


class MetricsEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Timestamp):
            return obj.__float__()
        return json.JSONEncoder.default(self, obj)


class JSONMetricsResource(Resource):
    """
    Twisted ``Resource`` that serves prometheus in JSON.
    """
    isLeaf = True

    def __init__(self, registry=REGISTRY):
        super().__init__()
        self.registry = registry

    def render_GET(self, request):
        request.setHeader(b'Content-Type', "text/json")
        return self.generate_latest_json()

    @staticmethod
    def get_exemplar(sample, metric):
        if not sample.exemplar:
            return {}
        elif metric.type not in ('histogram', 'gaugehistogram') or not sample.name.endswith('_bucket'):
            raise ValueError(
                "Metric {} has exemplars, but is not a "
                "histogram bucket".format(metric.name)
            )
        return {
            "labels": sample.exemplar.labels,
            "value": floatToGoString(sample.exemplar.value),
            "timestamp": sample.exemplar.timestamp
        }

    def get_sample(self, sample, metric):
        return {
            "sample_name": sample.name,
            "labels": sample.labels,
            "value": floatToGoString(sample.value),
            "timestamp": sample.timestamp,
            "exemplar": self.get_exemplar(sample, metric)
        }

    def get_metric(self, metric):
        return {
            "samples": [self.get_sample(sample, metric) for sample in metric.samples],
            "help": metric.documentation,
            "type": metric.type
        }

    def generate_latest_json(self):
        """
        Returns the prometheus from the registry
        in latest JSON format as a string.
        """
        output = {}
        for metric in self.registry.collect():
            try:
                output[metric.name] = self.get_metric(metric)
            except Exception as exception:
                exception.args = (exception.args or ('',)) + (metric,)
                raise

        json_dump = json.dumps(output, cls=MetricsEncoder).encode('utf-8')
        return json_dump


def collect_prometheus_metrics(metrics_collectors: List[MetricsCollector], node_metrics: Dict) -> None:
    for collector in metrics_collectors:
        collector.collect(node_metrics)


def initialize_prometheus_exporter(ursula, prometheus_config: PrometheusMetricsConfig) -> None:
    from prometheus_client.twisted import MetricsResource
    from twisted.web.resource import Resource
    from twisted.web.server import Site

    metrics_prefix = prometheus_config.metrics_prefix
    metrics_collectors = create_metrics_collectors(ursula, metrics_prefix)
    # initalized collectors
    node_metrics = {}
    for collector in metrics_collectors:
        node_metrics.update(collector.initialize(metrics_prefix=metrics_prefix, registry=ursula.registry))

    # TODO: was never used
    # "requests_counter": Counter(f'{metrics_prefix}_http_failures', 'HTTP Failures', ['method', 'endpoint']),

    # Scheduling
    metrics_task = task.LoopingCall(collect_prometheus_metrics,
                                    metrics_collectors=metrics_collectors,
                                    node_metrics=node_metrics)
    metrics_task.start(interval=10, now=False)  # TODO: make configurable

    # WSGI Service
    root = Resource()
    root.putChild(b'prometheus', MetricsResource())
    root.putChild(b'json_metrics', JSONMetricsResource())
    factory = Site(root)
    reactor.listenTCP(prometheus_config.port, factory, interface=prometheus_config.listen_address)


def create_metrics_collectors(ursula: Ursula, metrics_prefix: str) -> List[MetricsCollector]:
    collectors: List[MetricsCollector] = [UrsulaInfoMetricsCollector(ursula=ursula)]

    if not ursula.federated_only:
        # Blockchain prometheus
        collectors.append(BlockchainMetricsCollector(provider_uri=ursula.provider_uri))

        # Staker prometheus
        collectors.append(StakerMetricsCollector(staker_address=ursula.checksum_address,
                                                 contract_registry=ursula.registry))

        # Worker prometheus
        collectors.append(WorkerMetricsCollector(worker_address=ursula.worker_address,
                                                 contract_registry=ursula.registry))

        # WorkLock prometheus
        collectors.append(WorkLockMetricsCollector(staker_address=ursula.checksum_address,
                                                   contract_registry=ursula.registry))

        #
        # Events
        #

        # StakingAgent Events
        staking_events_collectors = create_staking_events_metric_collectors(ursula=ursula,
                                                                            metrics_prefix=metrics_prefix)
        collectors.extend(staking_events_collectors)

        # WorkLock Events
        worklock_events_collectors = create_worklock_events_metric_collectors(ursula=ursula,
                                                                              metrics_prefix=metrics_prefix)
        collectors.extend(worklock_events_collectors)

        # Policy Events
        policy_events_collectors = create_policy_events_metric_collectors(ursula=ursula,
                                                                          metrics_prefix=metrics_prefix)
        collectors.extend(policy_events_collectors)

    return collectors


def create_staking_events_metric_collectors(ursula: Ursula, metrics_prefix: str) -> List[MetricsCollector]:
    collectors: List[MetricsCollector] = []
    staking_agent = ContractAgency.get_agent(StakingEscrowAgent, registry=ursula.registry)

    # CommitmentMade
    collectors.append(EventMetricsCollector(
        event_name='CommitmentMade',
        event_args_config={
            "value": (Gauge, f'{metrics_prefix}_activity_confirmed_value', 'Activity confirmed with value of locked tokens'),
            "period": (Gauge, f'{metrics_prefix}_activity_confirmed_period', 'Activity confirmed period')
        },
        argument_filters={'staker': ursula.checksum_address},
        contract_agent=staking_agent))

    # Minted
    collectors.append(EventMetricsCollector(
        event_name='Minted',
        event_args_config={
            "value": (Gauge, f'{metrics_prefix}_mined_value', 'Mined value'),
            "period": (Gauge, f'{metrics_prefix}_mined_period', 'Mined period'),
            "block_number": (Gauge, f'{metrics_prefix}_mined_block_number', 'Mined block number')
        },
        argument_filters={'staker': ursula.checksum_address},
        contract_agent=staking_agent))

    # Slashed
    collectors.append(EventMetricsCollector(
        event_name='Slashed',
        event_args_config={
            "penalty": (Gauge, f'{metrics_prefix}_last_slashed_penalty', 'Penalty for slashing'),
            "block_number": (Gauge, f'{metrics_prefix}_last_slashed_penalty_block_number', 'Slashed penalty block number')
        },
        argument_filters={'staker': ursula.checksum_address},
        contract_agent=staking_agent))

    # RestakeSet
    collectors.append(ReStakeEventMetricsCollector(
        event_args_config={
            "reStake": (Gauge, f'{metrics_prefix}_restaking', 'Restake set')
        },
        argument_filters={'staker': ursula.checksum_address},
        staker_address=ursula.checksum_address,
        contract_agent=staking_agent))

    # WindDownSet
    collectors.append(WindDownEventMetricsCollector(
        event_args_config={
            "windDown": (Gauge, f'{metrics_prefix}_wind_down', 'is windDown')
        },
        argument_filters={'staker': ursula.checksum_address},
        staker_address=ursula.checksum_address,
        contract_agent=staking_agent))

    # WorkerBonded
    collectors.append(WorkerBondedEventMetricsCollector(
        event_args_config={
            "startPeriod": (Gauge, f'{metrics_prefix}_worker_set_start_period', 'New worker was set'),
            "block_number": (Gauge, f'{metrics_prefix}_worker_set_block_number', 'WorkerSet block number')
        },
        argument_filters={'staker': ursula.checksum_address},
        staker_address=ursula.checksum_address,
        worker_address=ursula.worker_address,
        contract_agent=staking_agent))

    return collectors


def create_worklock_events_metric_collectors(ursula: Ursula, metrics_prefix: str) -> List[MetricsCollector]:
    collectors: List[MetricsCollector] = []
    worklock_agent = ContractAgency.get_agent(WorkLockAgent, registry=ursula.registry)

    # Deposited
    collectors.append(EventMetricsCollector(
        event_name='Deposited',
        event_args_config={
            "value": (Gauge, f'{metrics_prefix}_worklock_deposited_value', 'Deposited value')
        },
        argument_filters={"sender": ursula.checksum_address},
        contract_agent=worklock_agent))

    # Claimed
    collectors.append(EventMetricsCollector(
        event_name='Claimed',
        event_args_config={
            "claimedTokens": (Gauge, f'{metrics_prefix}_worklock_claimed_claimedTokens', 'Claimed tokens value')
        },
        argument_filters={"sender": ursula.checksum_address},
        contract_agent=worklock_agent))

    # Bid/Refund (Modfy Same metric)
    collectors.append(BidEventMetricsCollector(
        event_args_config={
            "depositedETH": (Gauge, f'{metrics_prefix}_worklock_bid_depositedETH', 'Deposited ETH value')
        },
        argument_filters={"sender": ursula.checksum_address},
        staker_address=ursula.checksum_address,
        contract_agent=worklock_agent))

    collectors.append(RefundEventMetricsCollector(
        event_args_config={
            "depositedETH": (Gauge, f'{metrics_prefix}_worklock_bid_depositedETH', 'Deposited ETH value')
        },
        argument_filters={"sender": ursula.checksum_address},
        staker_address=ursula.checksum_address,
        contract_agent=worklock_agent))

    return collectors


def create_policy_events_metric_collectors(ursula: Ursula, metrics_prefix: str) -> List[MetricsCollector]:
    collectors: List[MetricsCollector] = []
    policy_manager_agent = ContractAgency.get_agent(PolicyManagerAgent, registry=ursula.registry)

    # Withdrawn
    collectors.append(EventMetricsCollector(
        event_name='Withdrawn',
        event_args_config={
            "value": (Gauge, f'{metrics_prefix}_policy_withdrawn_reward', 'Policy reward')
        },
        argument_filters={"recipient": ursula.checksum_address},
        contract_agent=policy_manager_agent))

    return collectors
