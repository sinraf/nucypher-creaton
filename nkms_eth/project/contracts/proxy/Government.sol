pragma solidity ^0.4.18;


import "../zeppelin/math/SafeMath.sol";
import "./Dispatcher.sol";
import "./Upgradeable.sol";

contract MinersEscrowInterface {
    function getLockedTokens(address _owner)
        public constant returns (uint256);
}

/**
* @notice Contract for version voting
**/
contract Government is Upgradeable {
    using SafeMath for uint256;

    enum VotingState {
        Active,
        UpgradeWaiting,
        Finished
    }

    enum VotingType {
        UpgradeGovernment,
        UpgradeEscrow,
        UpgradePolicyManager,
        RollbackGovernment,
        RollbackEscrow,
        RollbackPolicyManager
    }

    Dispatcher public escrow;
    Dispatcher public policyManager;
    uint256 public votingDurationSeconds;

    // last vote for specified voting number
    mapping(address => uint256) public lastVote;
    uint256 public votingNumber;
    uint256 public endVotingTimestamp;
    bool public upgradeFinished;
    VotingType public votingType;
    address public newAddress;
    // TODO maybe change to votes "for" only
    uint256 public votesFor;
    uint256 public votesAgainst;

    /**
    * @notice Contracts sets address for upgradeable contracts
    * @param _escrow The escrow dispatcher
    * @param _policyManager The policy manager dispatcher
    * @param _votingDurationHours Voting duration in hours
    **/
    function Government(
        Dispatcher _escrow,
        Dispatcher _policyManager,
        uint256 _votingDurationHours) {
        require(address(_escrow) != 0x0 &&
            address(_policyManager) != 0x0 &&
            _votingDurationHours != 0);
        escrow = _escrow;
        policyManager = _policyManager;
        votingDurationSeconds = _votingDurationHours.mul(1 hours);
    }

    /**
    * @notice Get voting state
    **/
    function getVotingState() public constant returns (VotingState) {
        if (block.timestamp <= endVotingTimestamp) {
            return VotingState.Active;
        }
        if (votesFor > votesAgainst && !upgradeFinished) {
            return VotingState.UpgradeWaiting;
        }
        return VotingState.Finished;
    }

    /**
    * @notice Create voting for upgrade or rollback
    * @param _votingType Voting type
    * @param _newAddress New address for upgrade. Not used for rollback
    **/
    function createVoting(
        VotingType _votingType,
        address _newAddress
    ) public {
        require(getVotingState() == VotingState.Finished);
        votingNumber = votingNumber.add(1);
        endVotingTimestamp = block.timestamp.add(votingDurationSeconds);
        upgradeFinished = false;
        votesFor = 0;
        votesAgainst = 0;
        votingType = _votingType;
        newAddress = _newAddress;
    }

    /**
    * @notice Vote
    **/
    function vote(bool voteFor) public {
        require(getVotingState() == VotingState.Active && lastVote[msg.sender] < votingNumber);
        var lockedTokens = MinersEscrowInterface(escrow).getLockedTokens(msg.sender);
        require(lockedTokens > 0);
        if (voteFor) {
            votesFor = votesFor.add(lockedTokens);
        } else {
            votesAgainst = votesAgainst.add(lockedTokens);
        }
        lastVote[msg.sender] = votingNumber;
    }

    /**
    * @notice Commit upgrade if voting is successful
    **/
    function commitUpgrade() public {
        require(getVotingState() == VotingState.UpgradeWaiting);
        upgradeFinished = true;
        if (votingType == VotingType.UpgradeGovernment) {
            Dispatcher(address(this)).upgrade(newAddress);
        } else if (votingType == VotingType.UpgradeEscrow) {
            escrow.upgrade(newAddress);
        } else if (votingType == VotingType.UpgradePolicyManager) {
            policyManager.upgrade(newAddress);
        } else if (votingType == VotingType.RollbackGovernment) {
            Dispatcher(address(this)).rollback();
        } else if (votingType == VotingType.RollbackEscrow) {
            escrow.rollback();
        } else if (votingType == VotingType.RollbackPolicyManager) {
            policyManager.rollback();
        }
    }

    function verifyState(address _testTarget) public constant {
        // TODO complete
    }

    function finishUpgrade(address _target) onlyOwner public {
        var government = Government(_target);
        escrow = government.escrow();
        policyManager = government.policyManager();
        votingDurationSeconds = government.votingDurationSeconds();
    }
}
