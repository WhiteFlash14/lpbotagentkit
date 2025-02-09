# File: python/cdp-agentkit-core/cdp_agentkit_core/actions/defi/rebalance_liquidity.py

from pydantic import BaseModel, Field
from cdp import Wallet
# from cdp_langchain import CdpTool
REBALANCE_LIQUIDITY_PROMPT = """
This tool creates or rebalances a Uniswap V3 liquidity position on Base mainnet.
If your agent’s wallet has not created a liquidity position in the specified pool, the tool will call the 
NonfungiblePositionManager contract’s mint method to create a new position with the provided tick range.
If you have already created a liquidity position, please indicate so and provide the existing token ID and liquidity.
In that case, the tool will remove liquidity, collect fees, burn the old position, and then mint a new liquidity position 
with the updated tick range.
"""

class RebalanceLiquidityInput(BaseModel):
    """
    Input schema for creating or rebalancing a Uniswap V3 liquidity position.
    """
    tick_a: int = Field(
        ...,
        description="The lower tick boundary for the liquidity position.",
        example=1000
    )
    tick_b: int = Field(
        ...,
        description="The upper tick boundary for the liquidity position.",
        example=2000
    )
    pool: str = Field(
        ...,
        description="The address of the Uniswap V3 pool on Base mainnet.",
        example="0x1234567890abcdef1234567890abcdef12345678"
    )
    token0: str = Field(
        ...,
        description="The ERC-20 contract address of token0 for the pool.",
        example="0xToken0Address"
    )
    token1: str = Field(
        ...,
        description="The ERC-20 contract address of token1 for the pool.",
        example="0xToken1Address"
    )
    amount0Desired: str = Field(
        ...,
        description="Desired amount for token0 (in wei) to supply as liquidity.",
        example="1000000000000000000"  # e.g. 1 token (assuming 18 decimals)
    )
    amount1Desired: str = Field(
        ...,
        description="Desired amount for token1 (in wei) to supply as liquidity.",
        example="2000000000000000000"  # e.g. 2 tokens
    )
    existing_position: bool = Field(
        ...,
        description="Indicate whether you have already created a liquidity position for this pool (True or False).",
        example=False
    )
    existing_tokenId: str = Field(
        None,
        description="If an existing liquidity position exists, provide its token ID.",
        example="1234"
    )
    existing_liquidity: str = Field(
        None,
        description="If an existing liquidity position exists, provide its liquidity amount (as a string).",
        example="1000"
    )

def rebalance_liquidity(
    wallet: Wallet,
    tick_a: int,
    tick_b: int,
    pool: str,
    token0: str,
    token1: str,
    amount0Desired: str,
    amount1Desired: str,
    existing_position: bool,
    existing_tokenId: str = None,
    existing_liquidity: str = None,
) -> str:
    """
    Creates or rebalances a Uniswap V3 liquidity position via the NonfungiblePositionManager contract.

    - If no existing position exists (existing_position is False), this function calls `mint` to create a new liquidity position.
    - If an existing position exists (existing_position is True), the function removes liquidity, collects fees,
      burns the old position, and then mints a new liquidity position with the updated tick range.

    Note: Token approvals must be handled manually via your MetaMask wallet.
    
    Args:
        wallet (Wallet): The user's wallet used to sign transactions.
        tick_a (int): The lower tick boundary.
        tick_b (int): The upper tick boundary.
        pool (str): The address of the Uniswap V3 pool.
        token0 (str): The address of token0.
        token1 (str): The address of token1.
        amount0Desired (str): The desired token0 amount (in wei) to deposit.
        amount1Desired (str): The desired token1 amount (in wei) to deposit.
        existing_position (bool): True if a liquidity position already exists; otherwise, False.
        existing_tokenId (str, optional): The token ID of the existing liquidity position (if any).
        existing_liquidity (str, optional): The liquidity amount of the existing position (if any).
        
    Returns:
        str: A status message indicating the result.
    """
    NM_POSITION_MANAGER = "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1"
    fee = 3000  # Fee tier: 0.3%
    deadline = 9999999999  # For example purposes—set this appropriately in production.

    if not existing_position:
        # Mint a new liquidity position.
        mint_args = {
            "token0": token0,
            "token1": token1,
            "fee": fee,
            "tickLower": tick_a,
            "tickUpper": tick_b,
            "amount0Desired": amount0Desired,
            "amount1Desired": amount1Desired,
            "amount0Min": "0",  # In production, set slippage tolerances.
            "amount1Min": "0",
            "recipient": wallet.address,
            "deadline": deadline,
            "pool": pool  # Optionally track pool info.
        }
        print("Minting new liquidity position...")
        invocation = wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="mint",
            args=mint_args
        )
        result = invocation.wait()
        tokenId = result.get("tokenId", "unknown")
        return f"Liquidity position created with token ID {tokenId} in pool {pool} with tick range [{tick_a}, {tick_b}]."
    else:
        # Rebalance an existing liquidity position.
        tokenId = existing_tokenId
        liquidity = existing_liquidity
        if not tokenId or int(liquidity) == 0:
            return "Existing liquidity position is invalid or has zero liquidity."

        # Remove liquidity from the existing position.
        decrease_args = {
            "tokenId": tokenId,
            "liquidity": liquidity,
            "amount0Min": "0",
            "amount1Min": "0",
            "deadline": deadline,
            "pool": pool
        }
        print(f"Removing liquidity from existing position (token ID {tokenId})...")
        invocation = wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="decreaseLiquidity",
            args=decrease_args
        )
        invocation.wait()

        # Collect fees from the old position.
        collect_args = {
            "tokenId": tokenId,
            "recipient": wallet.address,
            "amount0Max": amount0Desired,
            "amount1Max": amount1Desired
        }
        print("Collecting fees from the old position...")
        wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="collect",
            args=collect_args
        ).wait()

        # Burn the old position.
        print(f"Burning old liquidity position (token ID {tokenId})...")
        wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="burn",
            args={"tokenId": tokenId, "pool": pool}
        ).wait()

        # Mint a new liquidity position with updated tick range.
        mint_args = {
            "token0": token0,
            "token1": token1,
            "fee": fee,
            "tickLower": tick_a,
            "tickUpper": tick_b,
            "amount0Desired": amount0Desired,
            "amount1Desired": amount1Desired,
            "amount0Min": "0",
            "amount1Min": "0",
            "recipient": wallet.address,
            "deadline": deadline,
            "pool": pool
        }
        print("Minting new liquidity position with updated tick range...")
        invocation = wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="mint",
            args=mint_args
        )
        result = invocation.wait()
        new_tokenId = result.get("tokenId", "unknown")
        return (f"Liquidity position rebalanced. Old position (token ID {tokenId}) removed. "
                f"New liquidity position created with token ID {new_tokenId} in pool {pool} "
                f"with tick range [{tick_a}, {tick_b}].")

# --- Toolkit Registration ---


# rebalanceLiquidityTool = CdpTool(
#     name="rebalance_liquidity",
#     description=REBALANCE_LIQUIDITY_PROMPT,
#     cdp_agentkit_wrapper=agentkit,
#     args_schema=RebalanceLiquidityInput,
#     func=rebalance_liquidity,
# )
