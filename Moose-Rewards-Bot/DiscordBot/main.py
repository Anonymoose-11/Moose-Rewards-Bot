import decimal
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
import pymongo
from discord.ext import commands
import logging
import os
from dotenv import load_dotenv
from discord.ui import View, Button
from math import ceil

class TransactionView(View):
    def __init__(self, transactions, user_id, page_size=5):
        super().__init__(timeout=60)
        self.change = None
        self.balance = None
        self.transactions = transactions
        self.user_id = user_id
        self.page_size = page_size
        self.current_page = 0
        self.total_pages = ceil(len(transactions) / page_size)

        self.prev_button = Button(label="⏮ Previous", style=discord.ButtonStyle.secondary)
        self.next_button = Button(label="Next ⏭", style=discord.ButtonStyle.secondary)

        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    async def send_page(self, interaction):
        start = self.current_page * self.page_size
        end = start + self.page_size
        entries = self.transactions[start:end]

        guild = interaction.guild
        lines = []

        def get_display_name(user_id):
            member = guild.get_member(user_id)
            if member:
                return f"<@{user_id}>"
            else:
                account = accounts.find_one({"uuid": user_id})
                return account["username"] if account else f"User({user_id})"

        # Transaction lines
        for tx in entries:
            amount = decimal.Decimal(tx["amount"])
            time = tx["timestamp"].astimezone().strftime("%Y-%m-%d %H:%M")

            sender_id = tx["sender_uuid"]
            recipient_id = tx["recipient_uuid"]

            if sender_id == 0:
                lines.append(f"🏦 **Deposit**: +£{amount:.2f} at `{time}`")
            elif recipient_id == 0:
                lines.append(f"🏦 **Withdrawal**: -£{amount:.2f} at `{time}`")
            elif sender_id == self.user_id:
                recipient_name = get_display_name(recipient_id)
                lines.append(f"❌ Sent **£{amount:.2f}** to {recipient_name} at `{time}`")
            else:
                sender_name = get_display_name(sender_id)
                lines.append(f"✅ Received **£{amount:.2f}** from {sender_name} at `{time}`")

        # Top line: balance and change
        summary = f"💼 **Balance:** £{self.balance:.2f} {self.change}\n"
        summary += "\n".join(lines) if lines else "No transactions."

        for i in range(5 - len(lines)):
            summary += "\n"

        summary += f"\n\nPage {self.current_page + 1}/{self.total_pages}"

        await interaction.response.edit_message(content=summary, view=self)

    async def prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            await self.send_page(interaction)
        await self.send_page(interaction)

    async def next_page(self, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.send_page(interaction)
        await self.send_page(interaction)

    async def format_page(self, menu, entries):
        lines = []
        for tx in entries:
            is_sender = tx["sender_uuid"] == self.user_id
            other_id = tx["recipient_uuid"] if is_sender else tx["sender_uuid"]
            other_user = await menu.ctx.guild.fetch_member(other_id)
            other_name = other_user.display_name if other_user else "Unknown"
            amount = decimal.Decimal(tx["amount"])
            time = tx["timestamp"].astimezone().strftime("%Y-%m-%d %H:%M")

            symbol = "❌ Sent" if is_sender else "✅ Received"
            sign = "-" if is_sender else "+"
            lines.append(f"{symbol} **£{amount:.2f}** to/from **{other_name}** at `{time}`")

        return "\n".join(lines)

class Client(commands.Bot):
    async def on_ready(self):
        #await client.tree.sync()

        print(f"{client.user.name} is online")
        try:
            guild = discord.Object(id=int(os.getenv("GUILD_ID")))

            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} commands on guild {guild.id}")
        except Exception as e:
            print(f"Failed to sync guild: {e}")

load_dotenv()

GUILD_ID = discord.Object(id=int(os.getenv("GUILD_ID")))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))

token = os.getenv('DISCORD_TOKEN')

mongoClient = pymongo.MongoClient(
      "mongodb+srv://Anonymoose:e3mZCvtQt5UlD2Zx@idb-database.ssxj1mg.mongodb.net/?retryWrites=true&w=majority&appName=IDB-Database")
db = mongoClient["bankDB"]
accounts = db["accounts"]
transactions = db["transactions"]
loans = db["loans"]

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = Client(command_prefix='/', intents=intents)

@client.tree.command(name="statement", description="Shows your financial statement", guild=GUILD_ID)
async def statement(interaction: discord.Interaction):
    user_id = interaction.user.id
    user_account = accounts.find_one({"uuid": user_id})
    user_loans = list(loans.find({"uuid": user_id}))

    if not user_account:
        await interaction.response.send_message("You're not registered in the bank yet!", ephemeral=True)
        return

    username = user_account["username"]
    cash_balance = decimal.Decimal(user_account["balance"])
    investment_value = decimal.Decimal(user_account["investments"] if user_account["investments"] is not None else 0)

    # Format loan descriptions
    loan_lines = []
    for loan in user_loans:
        amount = decimal.Decimal(loan.get("amount", 0))
        rate = loan.get("interest_rate", 0)
        interval = loan.get("interval", "unknown interval")
        loan_lines.append(f"Loan of £{amount:.2f} with {rate}% every {interval}")

    loan_summary = "\n".join(loan_lines) if loan_lines else "None"

    summary = (
        f"**User Name:** {username}\n"
        f"💷 **Cash Balance:** £{cash_balance:.2f}\n"
        f"📈 **Investment Value:** £{investment_value:.2f}\n\n"
        f"📑 **Pending Payments:**\n{loan_summary}"
    )

    await interaction.response.send_message(summary, ephemeral=True)

@client.tree.command(name="pay", description="Pay another user by Discord", guild = GUILD_ID)
@app_commands.describe(recipient="Discord Member", amount="Amount to pay")
async def pay(interaction: discord.Interaction, recipient: discord.Member, amount: str):
    amount = decimal.Decimal(amount)
    sender_id = interaction.user.id

    # Find the sender
    if amount < decimal.Decimal("0.01"):
        await interaction.response.send_message("Please enter an amount greater than zero.", ephemeral=True)
        return None

    sender = accounts.find_one({"uuid": sender_id})

    if not sender:
        await interaction.response.send_message("You're not registered in the bank yet!", ephemeral=True)
        return None

    if sender["balance"] < amount:
        await interaction.response.send_message("You don't have enough balance.", ephemeral=True)
        return None

    # Try finding recipient by ID or username
    user_to = accounts.find_one({"uuid": recipient.id})

    if not user_to:
        await interaction.response.send_message("This user does not have an account.", ephemeral=True)
        return

    # Update balances
    accounts.update_one({"uuid": sender_id}, {"$inc": {"balance": float(-amount)}})
    accounts.update_one({"uuid": user_to["uuid"]}, {"$inc": {"balance": float(amount)}})

    transactions.insert_one({
        "sender_uuid": sender_id,
        "recipient_uuid": user_to["uuid"],
        "amount": float(amount),
        "timestamp": datetime.now(timezone.utc)
    })

    await interaction.response.send_message(
        f"{interaction.user.display_name} paid {user_to['username']} **£{amount}**!"
    )

@client.tree.command(name="transactions", description="View your balance changes and recent transactions", guild=GUILD_ID)
@app_commands.describe(range="Time range to calculate change")
@app_commands.choices(range=[
    app_commands.Choice(name="Day", value="day"),
    app_commands.Choice(name="Week", value="week"),
    app_commands.Choice(name="Month", value="month"),
    app_commands.Choice(name="Year", value="year")
])
async def list_transactions(interaction: discord.Interaction, range: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)

    user_id = interaction.user.id
    now = datetime.now(timezone.utc)

    if range.value == "day":
        start_date = now - timedelta(days=1)
    elif range.value == "week":
        start_date = now - timedelta(weeks=1)
    elif range.value == "month":
        start_date = now - timedelta(days=30)
    elif range.value == "year":
        start_date = now - timedelta(days=365)

    user_account = accounts.find_one({"uuid": user_id})
    if not user_account:
        await interaction.followup.send("You're not registered in the bank yet!", ephemeral=True)
        return

    balance = user_account["balance"]

    txs = list(transactions.find({
        "$or": [
            {"sender_uuid": user_id},
            {"recipient_uuid": user_id}
        ],
        "timestamp": {"$gte": start_date}
    }).sort("timestamp", -1))

    total_in = sum(tx["amount"] for tx in txs if tx["recipient_uuid"] == user_id)
    total_out = sum(tx["amount"] for tx in txs if tx["sender_uuid"] == user_id)
    net = decimal.Decimal(total_in) - decimal.Decimal(total_out)
    net_str = "("
    net_str += f"+£{net:.2f}" if net >= 0 else f"-£{abs(net):.2f}"
    net_str += f" in the last {range.name.lower()})"

    header = f"💼 **Balance:** £{balance:.2f} {net_str}\n\n"

    if not txs:
        await interaction.followup.send(header + "No transactions found.", ephemeral=True)
        return

    view = TransactionView(txs, user_id)
    view.balance = balance
    view.change = net_str
    await interaction.followup.send(header, view=view, ephemeral=True)
    await view.send_page(await interaction.original_response())

@client.tree.command(name="balance", description="Shows your balance", guild = GUILD_ID)
async def balance(interaction: discord.Interaction):
    sender_id = interaction.user.id

    sender = accounts.find_one({"uuid": sender_id})

    if not sender:
        await interaction.response.send_message("You're not registered in the bank yet!", ephemeral=True)
        return

    await interaction.response.send_message(f"Current balance: **£{sender['balance']}**", ephemeral=True)

@client.tree.command(name="withdraw", description="Open a ticket to withdraw money", guild = GUILD_ID)
@app_commands.describe(amount="Amount to withdraw")
async def withdraw(interaction: discord.Interaction, amount: str):
    await createTicket(interaction, f"withdrawal of **£{amount}**")

@client.tree.command(name="deposit", description="Open a ticket to deposit money", guild = GUILD_ID)
@app_commands.describe(amount="Amount to deposit")
async def deposit(interaction: discord.Interaction, amount: str):
    await createTicket(interaction, f"deposit of **£{amount}**")

async def createTicket(interaction: discord.Interaction, reason: str):
    guild = interaction.guild
    author = interaction.user

    tickets_category_id = int(os.getenv("TICKET_CATEGORY_ID"))
    category = discord.utils.get(guild.categories, id=tickets_category_id)

    admin_role = discord.utils.get(guild.roles, name="Admin")

    # Check if a ticket already exists
    existing_channel = discord.utils.get(guild.text_channels, name=f"ticket-{author.name.lower()}")
    if existing_channel:
        await interaction.response.send_message("You already have an open ticket.", ephemeral=True)
        return

    # Define permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }

    # Create the channel
    channel = await guild.create_text_channel(
        name=f"ticket-{author.name}",
        category=category,
        overwrites=overwrites,
        reason="Support ticket"
    )

    await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
    await channel.send(f"{author.mention} your ticket has been created for a(n) {reason}. A teller will be with you shortly.")

@client.tree.command(name="pgc", description="Apply for a Principal-Guaranteed-Certificate", guild = GUILD_ID)
async def pgc(interaction: discord.Interaction):
    await createTicket(interaction, f"application for a Principal-Guaranteed-Certificate")

@client.tree.command(name="register", description="Register your account", guild = GUILD_ID)
async def register(interaction: discord.Interaction):
    await createTicket(interaction, f"account registration of **{interaction.user.display_name}**")

@client.tree.command(name="close", description="Closes your ticket", guild=GUILD_ID)
async def close_ticket(interaction: discord.Interaction, reason: str):
    channel = interaction.channel
    author = interaction.user
    guild = interaction.guild

    # Must be a ticket channel
    if not channel.name.startswith("ticket-"):
        await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
        return

    # Check if the user is the ticket owner or an Admin
    admin_role = discord.utils.get(guild.roles, name="Admin")
    ticket_owner_name = channel.name.replace("ticket-", "")
    is_admin = admin_role in author.roles if admin_role else False
    is_owner = ticket_owner_name.lower() == author.name.lower()

    if not (is_admin or is_owner):
        await interaction.response.send_message("You don't have permission to close this ticket.", ephemeral=True)
        return

    await interaction.response.send_message("Closing this ticket... 👋", ephemeral=True)
    await channel.send(f"Ticket closed by {author.mention}. Deleting channel...")

    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"📁 Ticket **#{channel.name}** closed by **{author.display_name}**\n**Reason:** {reason}")

    await channel.delete()

# Admin commands

@app_commands.checks.has_role("Admin")
@client.tree.command(name="register_admin", description="Register a user into the bank", guild = GUILD_ID)
@app_commands.describe(member="Discord Member", username="Minecraft username")
async def admin(interaction: discord.Interaction, member: discord.Member, username: str):
    account = accounts.find_one({"uuid": member.id})

    if account:
        await interaction.response.send_message("User already registered.", ephemeral=True)
        return

    accounts.insert_one({"uuid": member.id, "username": username, "balance": 0, "investments": None})
    await interaction.response.send_message(f"User **{username}** registered!", ephemeral=True)

@app_commands.checks.has_role("Admin")
@client.tree.command(name="remove", description="Removes balance", guild = GUILD_ID)
@app_commands.describe(member="Discord Member", amount="Amount to take")
async def remove_balance(interaction: discord.Interaction, member: discord.Member, amount: str):
    amount = decimal.Decimal(amount)
    account = accounts.find_one({"uuid": member.id})

    if not account:
        await interaction.response.send_message("User is not registered in the bank!", ephemeral=True)
        return

    accounts.update_one({"uuid": member.id}, {"$inc": {"balance": float(-amount)}})
    await interaction.response.send_message(f"Removed **£{amount}** from {member.display_name}!", ephemeral=True)

    transactions.insert_one({
        "sender_uuid": 0,
        "recipient_uuid": member.id,
        "amount": float(amount),
        "timestamp": datetime.now(timezone.utc)
    })

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"🛑 **{interaction.user}** removed **£{amount}** from **{member}**")

@app_commands.checks.has_role("Admin")
@client.tree.command(name="give", description="Gives balance", guild = GUILD_ID)
@app_commands.describe(member="Discord Member", amount="Amount to give")
async def remove_balance(interaction: discord.Interaction, member: discord.Member, amount: str):
    amount = decimal.Decimal(amount)
    account = accounts.find_one({"uuid": member.id})

    if not account:
        await interaction.response.send_message("User is not registered in the bank!", ephemeral=True)
        return

    accounts.update_one({"uuid": member.id}, {"$inc": {"balance": float(amount)}})
    await interaction.response.send_message(f"Gave **£{amount}** to {member.display_name}!", ephemeral=True)

    transactions.insert_one({
        "sender_uuid": 0,
        "recipient_uuid": member.id,
        "amount": float(amount),
        "timestamp": datetime.now(timezone.utc)
    })

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"✅ **{interaction.user}** gave **£{amount}** to **{member}**")

client.run(token, log_handler=handler, log_level=logging.DEBUG)