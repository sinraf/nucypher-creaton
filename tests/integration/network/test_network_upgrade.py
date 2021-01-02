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


import os
import pytest_twisted
import requests
from cryptography.hazmat.primitives import serialization
from twisted.internet import threads

from nucypher.characters.lawful import Ursula
from nucypher.datastore.models import PolicyArrangement
from tests.utils.ursula import make_federated_ursulas


def test_alice_enacts_policies_in_policy_group_via_rest(enacted_federated_policy):
    """
    Now that Alice has made a PolicyGroup, she can enact its policies, using Ursula's Public Key to encrypt each offer
    and transmitting them via REST.
    """
    for ursula, kfrag in enacted_federated_policy._enacted_arrangements.items():
        arrangement = enacted_federated_policy._accepted_arrangements[ursula]
        with ursula.datastore.describe(PolicyArrangement, arrangement.id.hex()) as policy_arrangement:
            the_kfrag = policy_arrangement.kfrag
        assert kfrag == the_kfrag


@pytest_twisted.inlineCallbacks
def test_federated_nodes_connect_via_tls_and_verify(lonely_ursula_maker):
    node = lonely_ursula_maker(quantity=1).pop()
    node_deployer = node.get_deployer()

    node_deployer.addServices()
    node_deployer.catalogServers(node_deployer.hendrix)
    node_deployer.start()

    cert = node_deployer.cert.to_cryptography()
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)

    def check_node_with_cert(node, cert_file):
        response = requests.get("https://{}/public_information".format(node.rest_url()), verify=cert_file)
        ursula = Ursula.from_bytes(response.content)
        assert ursula == node

    try:
        with open("test-cert", "wb") as f:
            f.write(cert_bytes)
        yield threads.deferToThread(check_node_with_cert, node, "test-cert")
    finally:
        os.remove("test-cert")
