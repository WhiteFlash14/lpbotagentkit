# File: python/cdp-agentkit-core/tests/actions/defi/test_rebalance_liquidity.py

import pytest
from unittest.mock import patch, MagicMock
from cdp_agentkit_core.actions.defi.rebalance_liquidity import rebalance_liquidity

# Constants used for testing
MOCK_POOL = "0xPoolAddress"
MOCK_TOKEN0 = "0xToken0Address"
MOCK_TOKEN1 = "0xToken1Address"
MOCK_AMOUNT0 = "1000000000000000000"  # 1 token in wei
MOCK_AMOUNT1 = "2000000000000000000"  # 2 tokens in wei
MOCK_TICK_A = 1000
MOCK_TICK_B = 2000
MOCK_OLD_TOKEN_ID = "1234"
MOCK_NEW_TOKEN_ID = "5678"
MOCK_LIQUIDITY = "1000"

# --- Fixtures ---

@pytest.fixture
def wallet_factory():
    """
    Returns a dummy wallet instance.
    The dummy wallet will have the minimal attributes needed by our action.
    """
    class DummyWallet:
        def __init__(self):
            self.address = "0xDummyWalletAddress"
            self.network_id = "base_mainnet"

        def invoke_contract(self, contract_address, method, args):
            # The actual implementation will be patched in tests.
            pass

    return lambda: DummyWallet()

@pytest.fixture
def contract_invocation_factory():
    """
    Returns a dummy contract invocation object with a wait() method.
    """
    def _factory():
        dummy = MagicMock()
        dummy.wait = MagicMock()
        return dummy
    return _factory

# --- Test Cases ---

def test_rebalance_new_position(wallet_factory, contract_invocation_factory):
    """
    Test the branch where no existing liquidity position exists.
    The action should call the mint method and return a new token ID.
    """
    mock_wallet = wallet_factory()
    mock_contract_invocation = contract_invocation_factory()
    # Simulate that the mint call returns a dict with tokenId = MOCK_NEW_TOKEN_ID.
    mock_contract_invocation.wait.return_value = {"tokenId": MOCK_NEW_TOKEN_ID}

    with patch.object(
        mock_wallet, "invoke_contract", return_value=mock_contract_invocation
    ) as mock_invoke:
        response = rebalance_liquidity(
            wallet=mock_wallet,
            tick_a=MOCK_TICK_A,
            tick_b=MOCK_TICK_B,
            pool=MOCK_POOL,
            token0=MOCK_TOKEN0,
            token1=MOCK_TOKEN1,
            amount0Desired=MOCK_AMOUNT0,
            amount1Desired=MOCK_AMOUNT1,
            existing_position=False  # Indicate no existing position.
        )

        expected_response = (
            f"Liquidity position created with token ID {MOCK_NEW_TOKEN_ID} in pool {MOCK_POOL} with tick range "
            f"[{MOCK_TICK_A}, {MOCK_TICK_B}]."
        )
        assert response == expected_response

        # Verify that invoke_contract was called once with method "mint" and the expected args.
        mock_invoke.assert_called_once_with(
            contract_address="0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
            method="mint",
            args={
                "token0": MOCK_TOKEN0,
                "token1": MOCK_TOKEN1,
                "fee": 3000,
                "tickLower": MOCK_TICK_A,
                "tickUpper": MOCK_TICK_B,
                "amount0Desired": MOCK_AMOUNT0,
                "amount1Desired": MOCK_AMOUNT1,
                "amount0Min": "0",
                "amount1Min": "0",
                "recipient": mock_wallet.address,
                "deadline": 9999999999,
                "pool": MOCK_POOL,
            },
        )

def test_rebalance_existing_position(wallet_factory, contract_invocation_factory):
    """
    Test the branch where an existing liquidity position is present.
    The action should remove liquidity, collect fees, burn the old position, then mint a new one.
    """
    mock_wallet = wallet_factory()
    # We'll simulate four sequential calls: decreaseLiquidity, collect, burn, then mint.
    mock_decrease = contract_invocation_factory()
    mock_collect = contract_invocation_factory()
    mock_burn = contract_invocation_factory()
    mock_mint = contract_invocation_factory()
    # The final mint call returns a new tokenId.
    mock_mint.wait.return_value = {"tokenId": MOCK_NEW_TOKEN_ID}

    # Set side_effect to simulate the sequential calls.
    with patch.object(
        mock_wallet, "invoke_contract", side_effect=[mock_decrease, mock_collect, mock_burn, mock_mint]
    ) as mock_invoke:
        response = rebalance_liquidity(
            wallet=mock_wallet,
            tick_a=MOCK_TICK_A,
            tick_b=MOCK_TICK_B,
            pool=MOCK_POOL,
            token0=MOCK_TOKEN0,
            token1=MOCK_TOKEN1,
            amount0Desired=MOCK_AMOUNT0,
            amount1Desired=MOCK_AMOUNT1,
            existing_position=True,          # Indicates an existing position.
            existing_tokenId=MOCK_OLD_TOKEN_ID,  # Provide the old token ID.
            existing_liquidity=MOCK_LIQUIDITY  # Provide the liquidity of the existing position.
        )

        expected_response = (
            f"Liquidity position rebalanced. Old position (token ID {MOCK_OLD_TOKEN_ID}) removed. "
            f"New liquidity position created with token ID {MOCK_NEW_TOKEN_ID} in pool {MOCK_POOL} "
            f"with tick range [{MOCK_TICK_A}, {MOCK_TICK_B}]."
        )
        assert response == expected_response

        # Verify that invoke_contract was called four times.
        assert mock_invoke.call_count == 4

        # Optionally, check that the first call (decreaseLiquidity) has expected arguments.
        decrease_call = mock_invoke.call_args_list[0]
        assert decrease_call.kwargs["contract_address"] == "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1"
        assert decrease_call.kwargs["method"] == "decreaseLiquidity"
        assert decrease_call.kwargs["args"]["tokenId"] == MOCK_OLD_TOKEN_ID
        assert decrease_call.kwargs["args"]["liquidity"] == MOCK_LIQUIDITY
