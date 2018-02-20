import pytest
from ethereum.tester import TransactionFailed
from populus.contracts.contract import PopulusContract


NULL_ADDR = '0x' + '0' * 40

ACTIVE_STATE = 0
UPGRADE_WAITING_STATE = 1
FINISHED_STATE = 2

UPGRADE_GOVERNMENT = 0
UPGRADE_ESCROW = 1
UPGRADE_POLICY_MANAGER = 2
ROLLBACK_GOVERNMENT = 3
ROLLBACK_ESCROW = 4
ROLLBACK_POLICY_MANAGER = 5


# TODO extract method
def wait_time(chain, wait_hours):
    web3 = chain.web3
    step = 50
    end_timestamp = web3.eth.getBlock(web3.eth.blockNumber).timestamp + wait_hours * 60 * 60
    while web3.eth.getBlock(web3.eth.blockNumber).timestamp < end_timestamp:
        chain.wait.for_block(web3.eth.blockNumber + step)


@pytest.fixture()
def escrow(web3, chain):
    creator = web3.eth.accounts[0]
    node1 = web3.eth.accounts[1]
    node2 = web3.eth.accounts[2]
    node3 = web3.eth.accounts[3]

    # Creator deploys the escrow
    escrow_library, _ = chain.provider.get_or_deploy_contract(
        'MinersEscrowV1Test', deploy_args=[
            [node1, node2, node3], [1, 2, 3]],
        deploy_transaction={'from': creator})
    escrow_dispatcher, _ = chain.provider.deploy_contract(
        'Dispatcher', deploy_args=[escrow_library.address],
        deploy_transaction={'from': creator})
    escrow = web3.eth.contract(
        escrow_library.abi,
        escrow_dispatcher.address,
        ContractFactoryClass=PopulusContract)
    return escrow


@pytest.fixture()
def policy_manager(web3, chain):
    creator = web3.eth.accounts[0]
    # Creator deploys the escrow
    policy_manager, _ = chain.provider.get_or_deploy_contract(
        'PolicyManagerV1Test', deploy_transaction={'from': creator})
    dispatcher, _ = chain.provider.deploy_contract(
        'Dispatcher', deploy_args=[policy_manager.address],
        deploy_transaction={'from': creator})
    return dispatcher


def test_voting(web3, chain, escrow, policy_manager):
    creator = web3.eth.accounts[0]
    node1 = web3.eth.accounts[1]
    node2 = web3.eth.accounts[2]
    node3 = web3.eth.accounts[3]

    # Deploy contract
    government_library, _ = chain.provider.get_or_deploy_contract(
        'Government', deploy_args=[escrow.address, policy_manager.address, 1],
        deploy_transaction={'from': creator})
    government_dispatcher, _ = chain.provider.deploy_contract(
        'Dispatcher', deploy_args=[government_library.address],
        deploy_transaction={'from': creator})
    government = web3.eth.contract(
        government_library.abi,
        government_dispatcher.address,
        ContractFactoryClass=PopulusContract)

    # Transfer ownership
    tx = government.transact({'from': creator}).transferOwnership(government.address)
    chain.wait.for_receipt(tx)

    # Check that there are no voting before it's creation
    assert FINISHED_STATE == government.call().getVotingState()
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': node1}).vote(True)
        chain.wait.for_receipt(tx)
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': creator}).commitUpgrade()
        chain.wait.for_receipt(tx)

    # Deploy second version of the government contract
    government_library_v2, _ = chain.provider.deploy_contract(
        'Government', deploy_args=[escrow.address, policy_manager.address, 1],
        deploy_transaction={'from': creator})
    assert government_library.address.lower() != government_library_v2.address.lower()

    # Create voting for update Government contract
    tx = government.transact({'from': creator}).createVoting(
        UPGRADE_GOVERNMENT, government_library_v2.address)
    chain.wait.for_receipt(tx)
    assert 1 == government.call().votingNumber()
    assert UPGRADE_GOVERNMENT == government.call().votingType()
    assert government_library_v2.address.lower() == government.call().newAddress().lower()
    assert ACTIVE_STATE == government.call().getVotingState()
    assert 0 == government.call().votesFor()
    assert 0 == government.call().votesAgainst()

    # Can't commit upgrade before end of voting
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': creator}).commitUpgrade()
        chain.wait.for_receipt(tx)
    # Can't create new voting before end of previous voting
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': creator}).createVoting(
            UPGRADE_GOVERNMENT, government_library_v2.address)
        chain.wait.for_receipt(tx)

    # Nodes vote against update
    tx = government.transact({'from': node1}).vote(True)
    chain.wait.for_receipt(tx)
    assert 1 == government.call().votesFor()
    assert 0 == government.call().votesAgainst()
    tx = government.transact({'from': node2}).vote(False)
    chain.wait.for_receipt(tx)
    assert 1 == government.call().votesFor()
    assert 2 == government.call().votesAgainst()
    assert ACTIVE_STATE == government.call().getVotingState()

    # Can't vote again
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': node2}).vote(False)
        chain.wait.for_receipt(tx)

    # Wait until the end of voting
    wait_time(chain, 1)
    assert FINISHED_STATE == government.call().getVotingState()
    assert government_library.address.lower() == government_dispatcher.call().target().lower()
    assert 1 == government.call().votingNumber()

    # Can't vote after the ending
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': node3}).vote(False)
        chain.wait.for_receipt(tx)
    # Can't commit upgrade because nodes votes against upgrade
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': creator}).commitUpgrade()
        chain.wait.for_receipt(tx)

    # Create voting for update Government contract again
    tx = government.transact({'from': node1}).createVoting(
        UPGRADE_GOVERNMENT, government_library_v2.address)
    chain.wait.for_receipt(tx)
    assert 2 == government.call().votingNumber()
    assert UPGRADE_GOVERNMENT == government.call().votingType()
    assert government_library_v2.address.lower() == government.call().newAddress().lower()
    assert ACTIVE_STATE == government.call().getVotingState()
    assert 0 == government.call().votesFor()
    assert 0 == government.call().votesAgainst()

    # Nodes vote for update
    tx = government.transact({'from': node1}).vote(False)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node2}).vote(True)
    chain.wait.for_receipt(tx)
    assert 2 == government.call().votesFor()
    assert 1 == government.call().votesAgainst()
    assert ACTIVE_STATE == government.call().getVotingState()

    # Wait until the end of voting
    wait_time(chain, 1)
    assert UPGRADE_WAITING_STATE == government.call().getVotingState()
    assert government_library.address.lower() == government_dispatcher.call().target().lower()
    assert 2 == government.call().votingNumber()

    # Can't vote after the ending
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': node3}).vote(True)
        chain.wait.for_receipt(tx)
    # Can't create new voting before upgrading
    with pytest.raises(TransactionFailed):
        tx = government.transact({'from': creator}).createVoting(
            UPGRADE_GOVERNMENT, government_library_v2.address)
        chain.wait.for_receipt(tx)

    # Commit upgrade
    tx = government.transact({'from': node2}).commitUpgrade()
    chain.wait.for_receipt(tx)
    assert FINISHED_STATE == government.call().getVotingState()
    assert government_library_v2.address.lower() == government_dispatcher.call().target().lower()

    # Create voting for update Government contract again without voting
    tx = government.transact({'from': node2}).createVoting(
        UPGRADE_GOVERNMENT, government_library.address)
    chain.wait.for_receipt(tx)
    assert 3 == government.call().votingNumber()
    assert ACTIVE_STATE == government.call().getVotingState()
    assert 0 == government.call().votesFor()
    assert 0 == government.call().votesAgainst()

    # Wait until the end of voting
    wait_time(chain, 1)
    assert FINISHED_STATE == government.call().getVotingState()

    # Create voting for update Government contract again with equal voting
    tx = government.transact({'from': node3}).createVoting(
        UPGRADE_GOVERNMENT, government_library.address)
    chain.wait.for_receipt(tx)
    assert 4 == government.call().votingNumber()
    assert ACTIVE_STATE == government.call().getVotingState()
    tx = government.transact({'from': node1}).vote(False)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node2}).vote(False)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node3}).vote(True)
    chain.wait.for_receipt(tx)
    assert 3 == government.call().votesFor()
    assert 3 == government.call().votesAgainst()

    # Wait until the end of voting
    wait_time(chain, 1)
    assert FINISHED_STATE == government.call().getVotingState()


def test_upgrade(web3, chain, escrow, policy_manager):
    creator = web3.eth.accounts[0]
    node1 = web3.eth.accounts[1]

    # Deploy contract
    government_library_v1, _ = chain.provider.get_or_deploy_contract(
        'Government', deploy_args=[escrow.address, policy_manager.address, 1],
        deploy_transaction={'from': creator})
    government_dispatcher, _ = chain.provider.deploy_contract(
        'Dispatcher', deploy_args=[government_library_v1.address],
        deploy_transaction={'from': creator})
    government = web3.eth.contract(
        government_library_v1.abi,
        government_dispatcher.address,
        ContractFactoryClass=PopulusContract)

    # Deploy second version of the government contract
    government_library_v2, _ = chain.provider.deploy_contract(
        'Government', deploy_args=[escrow.address, policy_manager.address, 1],
        deploy_transaction={'from': creator})
    # Get first version of the escrow contract
    escrow_library_v1 = chain.provider.get_contract('MinersEscrowV1Test')
    # Deploy second version of the escrow contract
    escrow_library_v2, _ = chain.provider.deploy_contract(
        'MinersEscrowV1Test', deploy_args=[[node1], [1]],
        deploy_transaction={'from': creator})
    # Get first version of the policy manager contract
    policy_manager_library_v1 = chain.provider.get_contract('PolicyManagerV1Test')
    # Deploy second version of the policy manager contract
    policy_manager_library_v2, _ = chain.provider.deploy_contract(
        'PolicyManagerV1Test', deploy_transaction={'from': creator})

    # Transfer ownership
    tx = government.transact({'from': creator}).transferOwnership(government.address)
    chain.wait.for_receipt(tx)
    tx = escrow.transact({'from': creator}).transferOwnership(government.address)
    chain.wait.for_receipt(tx)
    tx = policy_manager.transact({'from': creator}).transferOwnership(government.address)
    chain.wait.for_receipt(tx)

    # Vote and upgrade government contract
    tx = government.transact({'from': node1}).createVoting(
        UPGRADE_GOVERNMENT, government_library_v2.address)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node1}).vote(True)
    chain.wait.for_receipt(tx)
    wait_time(chain, 1)
    tx = government.transact({'from': node1}).commitUpgrade()
    chain.wait.for_receipt(tx)
    assert government_library_v2.address.lower() == government_dispatcher.call().target().lower()

    # Vote and rollback government contract
    tx = government.transact({'from': node1}).createVoting(ROLLBACK_GOVERNMENT, NULL_ADDR)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node1}).vote(True)
    chain.wait.for_receipt(tx)
    wait_time(chain, 1)
    tx = government.transact({'from': node1}).commitUpgrade()
    chain.wait.for_receipt(tx)
    assert government_library_v1.address.lower() == government_dispatcher.call().target().lower()

    # Vote and upgrade escrow contract
    tx = government.transact({'from': node1}).createVoting(
        UPGRADE_ESCROW, escrow_library_v2.address)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node1}).vote(True)
    chain.wait.for_receipt(tx)
    wait_time(chain, 1)
    tx = government.transact({'from': node1}).commitUpgrade()
    chain.wait.for_receipt(tx)
    assert escrow_library_v2.address.lower() == escrow.call().target().lower()

    # Vote and rollback escrow contract
    tx = government.transact({'from': node1}).createVoting(ROLLBACK_ESCROW, NULL_ADDR)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node1}).vote(True)
    chain.wait.for_receipt(tx)
    wait_time(chain, 1)
    tx = government.transact({'from': node1}).commitUpgrade()
    chain.wait.for_receipt(tx)
    assert escrow_library_v1.address.lower() == escrow.call().target().lower()

    # Vote and upgrade policy manager contract
    tx = government.transact({'from': node1}).createVoting(
        UPGRADE_POLICY_MANAGER, policy_manager_library_v2.address)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node1}).vote(True)
    chain.wait.for_receipt(tx)
    wait_time(chain, 1)
    tx = government.transact({'from': node1}).commitUpgrade()
    chain.wait.for_receipt(tx)
    assert policy_manager_library_v2.address.lower() == policy_manager.call().target().lower()

    # Vote and rollback policy manager contract
    tx = government.transact({'from': node1}).createVoting(ROLLBACK_POLICY_MANAGER, NULL_ADDR)
    chain.wait.for_receipt(tx)
    tx = government.transact({'from': node1}).vote(True)
    chain.wait.for_receipt(tx)
    wait_time(chain, 1)
    tx = government.transact({'from': node1}).commitUpgrade()
    chain.wait.for_receipt(tx)
    assert policy_manager_library_v1.address.lower() == policy_manager.call().target().lower()
