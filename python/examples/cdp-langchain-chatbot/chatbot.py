import os
import sys
import time

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from cdp_langchain.tools import CdpTool

# Import CDP Agentkit Langchain Extension.
from cdp_langchain.agent_toolkits import CdpToolkit
from cdp_langchain.utils import CdpAgentkitWrapper
from cdp import Wallet 
# Configure a file to persist the agent's CDP MPC Wallet Data.
wallet_data_file = "wallet_data.txt"
load_dotenv()
REBALANCE_LIQUIDITY_PROMPT = """
This tool creates or rebalances a Uniswap V3 liquidity position on Base sepolia.
If your agent’s wallet has never created a liquidity position in the specified pool, the tool will:
  1. Check that the required ERC-20 token approvals for token0 and token1 are in place (triggering approval transactions if needed).
  2. Call the NonfungiblePositionManager contract’s mint method to create a new liquidity position with the given tick range.
If a liquidity position already exists, the tool will remove liquidity (and burn the old position) before creating a new one with the updated tick range.
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
        description="The address of the Uniswap V3 pool on Base sepolia.",
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
        example="1000000000000000000"  # 1 token (assuming 18 decimals)
    )
    amount1Desired: str = Field(
        ...,
        description="Desired amount for token1 (in wei) to supply as liquidity.",
        example="2000000000000000000"  # 2 tokens (for example)
    )

def rebalance_liquidity(
    wallet: Wallet,
    tick_a: int,
    tick_b: int,
    pool: str,
    token0: str,
    token1: str,
    amount0Desired: str,
    amount1Desired: str
) -> str:
    """
    Creates or rebalances a Uniswap V3 liquidity position via the NonfungiblePositionManager contract.

    This function first checks whether a liquidity position exists for the given pool.
    - If no position exists, it verifies token approvals for token0 and token1 (triggering approval transactions if needed)
      and then calls `mint` to create a new liquidity position with the specified tick range.
    - If a position exists, it removes liquidity (using `decreaseLiquidity`), optionally collects fees,
      burns the old position, and then calls `mint` to create a new position with the updated tick range.

    Args:
        wallet (Wallet): The user's wallet used to sign transactions.
        tick_a (int): The lower tick boundary.
        tick_b (int): The upper tick boundary.
        pool (str): The address of the Uniswap V3 pool.
        token0 (str): The address of token0.
        token1 (str): The address of token1.
        amount0Desired (str): The desired token0 amount (in wei) to deposit.
        amount1Desired (str): The desired token1 amount (in wei) to deposit.

    Returns:
        str: A status message indicating the result.
    """
    # NonfungiblePositionManager contract address on Base sepolia
    NM_POSITION_MANAGER = "0x27F971cb582BF9E50F397e4d29a5C7A34f11faA2"
    fee = 3000  # Assuming a fee tier of 0.3%
    deadline = 9999999999  # Use a sufficiently high deadline (or calculate based on current time)

    # --- Helper: Check if a liquidity position exists ---
    # (Assume wallet.get_liquidity_position returns None if no position exists,
    #  or a dict with details if a position exists.)
    position = wallet.get_liquidity_position(pool)
    
    if not position:
        # No liquidity position exists—create one.

        # --- Step 1: Ensure token approvals for token0 and token1 ---
        # The user’s wallet must approve the NonfungiblePositionManager contract to spend tokens.
        if not wallet.has_token_approval(token0, NM_POSITION_MANAGER):
            print(f"Requesting approval for token0 ({token0})...")
            approval_tx = wallet.invoke_contract(
                contract_address=token0,
                method="approve",
                args={"spender": NM_POSITION_MANAGER, "value": amount0Desired}
            )
            approval_tx.wait()  # This will prompt the user to confirm via their wallet

        if not wallet.has_token_approval(token1, NM_POSITION_MANAGER):
            print(f"Requesting approval for token1 ({token1})...")
            approval_tx = wallet.invoke_contract(
                contract_address=token1,
                method="approve",
                args={"spender": NM_POSITION_MANAGER, "value": amount1Desired}
            )
            approval_tx.wait()

        # --- Step 2: Mint a new liquidity position ---
        mint_args = {
            "token0": token0,
            "token1": token1,
            "fee": fee,
            "tickLower": tick_a,
            "tickUpper": tick_b,
            "amount0Desired": amount0Desired,
            "amount1Desired": amount1Desired,
            "amount0Min": "0",  # In production, consider slippage tolerances
            "amount1Min": "0",
            "recipient": wallet.address,
            "deadline": deadline
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
        # A liquidity position exists—rebalance by removing the old position and creating a new one.
        tokenId = position.get("tokenId")
        liquidity = position.get("liquidity")
        if not tokenId or liquidity == "0":
            return "Existing liquidity position is invalid or has zero liquidity."

        # --- Step 1: Remove liquidity from the existing position ---
        decrease_args = {
            "tokenId": tokenId,
            "liquidity": liquidity,
            "amount0Min": "0",
            "amount1Min": "0",
            "deadline": deadline
        }
        print(f"Removing liquidity from existing position (token ID {tokenId})...")
        invocation = wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="decreaseLiquidity",
            args=decrease_args
        )
        invocation.wait()

        # --- Step 2 (optional): Collect fees ---
        collect_args = {
            "tokenId": tokenId,
            "recipient": wallet.address,
            "amount0Max": amount0Desired,  # These values can be adjusted as needed
            "amount1Max": amount1Desired
        }
        print("Collecting fees from the old position...")
        wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="collect",
            args=collect_args
        ).wait()

        # --- Step 3: Burn the old position ---
        print(f"Burning old liquidity position (token ID {tokenId})...")
        wallet.invoke_contract(
            contract_address=NM_POSITION_MANAGER,
            method="burn",
            args={"tokenId": tokenId}
        ).wait()

        # --- Step 4: Mint a new liquidity position with updated tick range ---
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
            "deadline": deadline
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

load_dotenv()
def initialize_agent():
    """Initialize the agent with CDP Agentkit."""
    # Initialize LLM.
    llm = ChatOpenAI(model="gpt-4o-mini")

    wallet_data = None

    if os.path.exists(wallet_data_file):
        with open(wallet_data_file) as f:
            wallet_data = f.read()

    # Configure CDP Agentkit Langchain Extension.
    values = {}
    if wallet_data is not None:
        # If there is a persisted agentic wallet, load it and pass to the CDP Agentkit Wrapper.
        values = {"cdp_wallet_data": wallet_data}

    agentkit = CdpAgentkitWrapper(**values)

    # persist the agent's CDP MPC Wallet Data.
    wallet_data = agentkit.export_wallet()
    with open(wallet_data_file, "w") as f:
        f.write(wallet_data)

    # Initialize CDP Agentkit Toolkit and get tools.
    cdp_toolkit = CdpToolkit.from_cdp_agentkit_wrapper(agentkit)
    tools = cdp_toolkit.get_tools()
    rebalanceLiquidityTool = CdpTool(
    name="rebalance_liquidity",
    description=REBALANCE_LIQUIDITY_PROMPT,
    cdp_agentkit_wrapper=agentkit,  # Use your instantiated agentkit wrapper
    args_schema=RebalanceLiquidityInput,
    func=rebalance_liquidity,
)     

    tools.append(rebalanceLiquidityTool)

    # Store buffered conversation history in memory.
    memory = MemorySaver()
    config = {"configurable": {"thread_id": "CDP Agentkit Chatbot Example!"}}

    # Create ReAct Agent using the LLM and CDP Agentkit tools.
    return create_react_agent(
        llm,
        tools=tools,
        checkpointer=memory,
        state_modifier=(
            "You are a helpful agent that can interact onchain using the Coinbase Developer Platform AgentKit. "
            "You are empowered to interact onchain using your tools. If you ever need funds, you can request "
            "them from the faucet if you are on network ID 'base-sepolia'. If not, you can provide your wallet "
            "details and request funds from the user. Before executing your first action, get the wallet details "
            "to see what network you're on. If there is a 5XX (internal) HTTP error code, ask the user to try "
            "again later. If someone asks you to do something you can't do with your currently available tools, "
            "you must say so, and encourage them to implement it themselves using the CDP SDK + Agentkit, "
            "recommend they go to docs.cdp.coinbase.com for more information. Be concise and helpful with your "
            "responses. Refrain from restating your tools' descriptions unless it is explicitly requested."
        ),
    ), config


# Autonomous Mode
def run_autonomous_mode(agent_executor, config, interval=10):
    """Run the agent autonomously with specified intervals."""
    print("Starting autonomous mode...")
    while True:
        try:
            # Provide instructions autonomously
            thought = (
                "Be creative and do something interesting on the blockchain. "
                "Choose an action or set of actions and execute it that highlights your abilities."
            )

            # Run agent in autonomous mode
            for chunk in agent_executor.stream(
                {"messages": [HumanMessage(content=thought)]}, config
            ):
                if "agent" in chunk:
                    print(chunk["agent"]["messages"][0].content)
                elif "tools" in chunk:
                    print(chunk["tools"]["messages"][0].content)
                print("-------------------")

            # Wait before the next action
            time.sleep(interval)

        except KeyboardInterrupt:
            print("Goodbye Agent!")
            sys.exit(0)


# Chat Mode
def run_chat_mode(agent_executor, config):
    """Run the agent interactively based on user input."""
    print("Starting chat mode... Type 'exit' to end.")
    while True:
        try:
            user_input = input("\nPrompt: ")
            if user_input.lower() == "exit":
                break

            # Run agent with the user's input in chat mode
            for chunk in agent_executor.stream(
                {"messages": [HumanMessage(content=user_input)]}, config
            ):
                if "agent" in chunk:
                    print(chunk["agent"]["messages"][0].content)
                elif "tools" in chunk:
                    print(chunk["tools"]["messages"][0].content)
                print("-------------------")

        except KeyboardInterrupt:
            print("Goodbye Agent!")
            sys.exit(0)


# Mode Selection
def choose_mode():
    """Choose whether to run in autonomous or chat mode based on user input."""
    while True:
        print("\nAvailable modes:")
        print("1. chat    - Interactive chat mode")
        print("2. auto    - Autonomous action mode")

        choice = input("\nChoose a mode (enter number or name): ").lower().strip()
        if choice in ["1", "chat"]:
            return "chat"
        elif choice in ["2", "auto"]:
            return "auto"
        print("Invalid choice. Please try again.")


def main():
    """Start the chatbot agent."""
    agent_executor, config = initialize_agent()

    mode = choose_mode()
    if mode == "chat":
        run_chat_mode(agent_executor=agent_executor, config=config)
    elif mode == "auto":
        run_autonomous_mode(agent_executor=agent_executor, config=config)


if __name__ == "__main__":
    print("Starting Agent...")
    main()
