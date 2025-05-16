from datetime import datetime, timedelta
from discord.ext import tasks
import discord
from discord import app_commands
import sqlite3
from discord.ext import commands
import logging
import os
from dotenv import load_dotenv
from discord.ui import View, Button

def init_db():
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            referrals INTEGER DEFAULT 8
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS point_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            points INTEGER NOT NULL,
            earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS store_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            cost INTEGER NOT NULL,
            description TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES store_items (id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

init_db()

@tasks.loop(hours=24)
async def cleanup_expired_points():
    deleted = remove_expired_points()
    print(f"[Point Cleanup] Removed {deleted} expired point entries.")

def remove_expired_points():
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM point_entries
        WHERE expires_at <= ?
    """, (datetime.now(),))

    deleted = cur.rowcount  # Number of rows deleted (for logging)
    conn.commit()
    conn.close()

    return deleted

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

        # Start cleanup task
        if not cleanup_expired_points.is_running():
            cleanup_expired_points.start()

load_dotenv()

GUILD_ID = discord.Object(id=int(os.getenv("GUILD_ID")))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))

token = os.getenv('DISCORD_TOKEN')


handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.reactions = True
intents.guilds = True
intents.message_content = True
intents.members = True

client = Client(command_prefix='/', intents=intents)

@client.event
async def on_raw_reaction_add(payload):
    if payload.member is None or payload.member.bot:
        return

    emoji = str(payload.emoji)
    if emoji != "🎫":
        return

    ticket_message_id = int(get_setting("ticket_prompt_message_id"))

    if payload.message_id != ticket_message_id:
        return

    guild = client.get_guild(payload.guild_id)
    member = payload.member
    channel = guild.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    # Remove the user's reaction
    await message.remove_reaction(payload.emoji, payload.member)

    # Check if the user already has an open ticket
    existing = discord.utils.get(guild.text_channels, name=f"ticket-{member.name.lower()}")
    if existing:
        return

    category_id = int(os.getenv("TICKET_CATEGORY_ID"))
    category = discord.utils.get(guild.categories, id=category_id)
    admin_role = discord.utils.get(guild.roles, name="Admin")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }

    channel = await guild.create_text_channel(
        name=f"ticket-{member.name}",
        category=category,
        overwrites=overwrites,
        reason="Support ticket"
    )

    await channel.send(f"{member.mention} your ticket has been created. A staff member will be with you shortly.")

def set_setting(key: str, value: str):
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
    conn.close()

def get_setting(key: str) -> str | None:
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def total_points(discord_id: str) -> int:
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(points)
        FROM point_entries
        WHERE discord_id = ?
        AND expires_at > CURRENT_TIMESTAMP
        """, (discord_id,))
    result = cur.fetchone()

    return result[0] if result else 0

def spend_points(discord_id: str, amount: int) -> bool:
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    # Fetch unexpired point entries in FIFO order
    cur.execute("""
        SELECT id, points FROM point_entries
        WHERE discord_id = ? AND expires_at > ?
        ORDER BY earned_at ASC
    """, (discord_id, datetime.now()))

    rows = cur.fetchall()
    remaining = amount

    for row in rows:
        entry_id, available = row

        if available > remaining:
            cur.execute("UPDATE point_entries SET points = points - ? WHERE id = ?", (remaining, entry_id))
        else:
            cur.execute("DELETE FROM point_entries WHERE id = ?", (entry_id,))
            remaining -= available
        if remaining == 0:
            break

    conn.commit()
    conn.close()

    return remaining == 0  # True if fully spent

@client.tree.command(name="points", description="Shows your balance", guild=GUILD_ID)
async def points(interaction: discord.Interaction):
    remove_expired_points()
    user_id = str(interaction.user.id)

    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE discord_id = ?", (user_id,))

    if not cur.fetchone():
        await interaction.response.send_message("You're not registered yet!", ephemeral=True)
        return

    conn.close()

    points = total_points(user_id)
    await interaction.response.send_message(f"Current points: **{points}**", ephemeral=True)

@client.tree.command(name="store", description="Opens the point store", guild = GUILD_ID)
async def store(interaction: discord.Interaction):
    user_id = str(interaction.user.id)

    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    # Get total available points]
    user_points = total_points(user_id)

    if user_points == 0:
        await interaction.response.send_message("You have no points!", ephemeral=True)
        conn.close()
        return

    # Fetch store items
    cur.execute("SELECT id, name, cost, description FROM store_items")
    items = cur.fetchall()

    if not items:
        await interaction.response.send_message("The store is currently empty.", ephemeral=True)
        conn.close()
        return

    embed = discord.Embed(title="🛒 Point Store", description=f"You have **{user_points}** points",
                          color=discord.Color.green())
    view = View()

    for item_id, name, cost, description in items:
        embed.add_field(name=f"{name} - {cost} pts", value=description, inline=False)

        async def buy_callback(interact: discord.Interaction, item_id=item_id, name=name, cost=cost):
            conn = sqlite3.connect("points.db")
            cur = conn.cursor()

            # Recalculate user's valid points
            total = total_points(user_id)
            if total < cost:
                await interact.response.send_message("You don't have enough points!", ephemeral=True)
                conn.close()
                return

            spend_points(user_id, cost)

            cur.execute("""
                INSERT INTO user_inventory (discord_id, item_id)
                VALUES (?, ?)
                """, (user_id, item_id))

            conn.commit()
            conn.close()
            await interact.response.send_message(f"You bought **{name}** for {cost} points!", ephemeral=True)

        button = Button(label=f"Buy {name}", style=discord.ButtonStyle.primary)
        button.callback = buy_callback
        view.add_item(button)

    conn.close()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@client.tree.command(name="register", description="Register for the rewards program", guild = GUILD_ID)
@app_commands.describe(username="Minecraft username")
async def register(interaction: discord.Interaction, username: str):
    discord_id = str(interaction.user.id)

    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    # Check if already registered
    cur.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,))
    if cur.fetchone():
        await interaction.response.send_message("You're already registered!", ephemeral=True)
    else:
        cur.execute("INSERT INTO users (discord_id, username) VALUES (?, ?)", (discord_id, username))
        conn.commit()
        await interaction.response.send_message(f"Registered {username} successfully!", ephemeral=True)

    conn.close()

@client.tree.command(name="referral", description="Register for the rewards program", guild = GUILD_ID)
@app_commands.describe(username="Minecraft username")
async def referral(interaction: discord.Interaction, username: str, member: discord.Member):
    discord_id = str(interaction.user.id)
    referral_id = str(member.id)
    amount = 50

    if discord_id == referral_id:
        await interaction.response.send_message("You cannot refer yourself!", ephemeral=True)
        return

    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    # Check if user is already registered
    cur.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,))
    if cur.fetchone():
        await interaction.response.send_message("You're already registered!", ephemeral=True)
        conn.close()
        return

    # Check if referral exists
    cur.execute("SELECT referrals FROM users WHERE discord_id = ?", (referral_id,))
    row = cur.fetchone()
    if not row:
        await interaction.response.send_message("The referred member is not registered!", ephemeral=True)
        conn.close()
        return
    if row[0] == 0:
        await interaction.response.send_message("The referred member cannot receive more referrals!", ephemeral=True)
        conn.close()
        return

    # Register new user
    cur.execute("INSERT INTO users (discord_id, username) VALUES (?, ?)", (discord_id, username))
    conn.commit()

    # Reward referrer
    now = datetime.now()
    expiry = now + timedelta(days=180)
    cur.execute("""
        INSERT INTO point_entries (discord_id, points, earned_at, expires_at)
        VALUES (?, ?, ?, ?)
    """, (referral_id, amount, now, expiry))

    cur.execute("UPDATE users SET referrals = referrals - 1 WHERE discord_id = ?", (referral_id,))
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"Registered successfully! {member.display_name} has earned {amount} points for referring you.", ephemeral=True)

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"✅ **{member}** got **{amount}** points from referring **{interaction.user}**.")

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
@client.tree.command(name="remove", description="Removes balance", guild = GUILD_ID)
@app_commands.describe(member="Discord Member", amount="Amount to take")
async def remove_balance(interaction: discord.Interaction, member: discord.Member, amount: int):
    user_id = str(member.id)

    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE discord_id = ?", (user_id,))
    if not cur.fetchone():
        await interaction.response.send_message("User not registered!", ephemeral=True)
        conn.close()
        return

    conn.commit()
    conn.close()

    spend_points(user_id, amount)

    await interaction.response.send_message(f"Removed **{amount}** points from {member.display_name}!", ephemeral=True)

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"🛑 **{interaction.user.mention}** removed **£{amount}** points from **{member.mention}**")

@app_commands.checks.has_role("Admin")
@client.tree.command(name="give", description="Gives balance", guild = GUILD_ID)
@app_commands.describe(member="Discord Member", amount="Amount to give")
async def give_balance(interaction: discord.Interaction, member: discord.Member, amount: int):
    user_id = str(member.id)

    now = datetime.now()
    expiry = now + timedelta(days=180)

    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE discord_id = ?", (user_id,))
    if not cur.fetchone():
        await interaction.response.send_message("User not registered!", ephemeral=True)
        conn.close()
        return

    cur.execute("""
                INSERT INTO point_entries (discord_id, points, earned_at, expires_at)
                VALUES (?, ?, ?, ?)
                """, (user_id, amount, now, expiry))

    conn.commit()
    conn.close()

    await interaction.response.send_message(f"Gave **{amount}** points to {member.display_name}!", ephemeral=True)

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"✅ **{interaction.user.mention}** gave **{amount}** points to **{member.mention}**")

@app_commands.checks.has_role("Admin")
@client.tree.command(name="additem", description="Add or update an item in the point store", guild=GUILD_ID)
@app_commands.describe(name="Name of the item", cost="Cost in points", description="Description of the item")
async def additem(interaction: discord.Interaction, name: str, cost: int, description: str):
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    # Check if item already exists (by name)
    cur.execute("SELECT id FROM store_items WHERE name = ?", (name,))
    row = cur.fetchone()

    if row:
        cur.execute("UPDATE store_items SET cost = ?, description = ? WHERE id = ?", (cost, description, row[0]))
        await interaction.response.send_message(f"Updated item **{name}** in the store.", ephemeral=True)
    else:
        cur.execute("INSERT INTO store_items (name, cost, description) VALUES (?, ?, ?)", (name, cost, description))
        await interaction.response.send_message(f"Added item **{name}** to the store.", ephemeral=True)

    conn.commit()
    conn.close()

@app_commands.checks.has_role("Admin")
@client.tree.command(name="removeitem", description="Remove an item in the point store", guild=GUILD_ID)
@app_commands.describe(name="Name of the item")
async def remove_item(interaction: discord.Interaction, name: str):
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    # Check if item exists (by name)
    cur.execute("SELECT id FROM store_items WHERE name = ?", (name,))
    row = cur.fetchone()

    if row:
        cur.execute("DELETE FROM store_items WHERE id = ?", (row[0]))
        await interaction.response.send_message(f"Removed item **{name}** from the store.", ephemeral=True)
    else:
        await interaction.response.send_message(f"No item named **{name}** found in the store.", ephemeral=True)

    conn.commit()
    conn.close()

@app_commands.checks.has_role("Admin")
@client.tree.command(name="inventory", description="View a user's purchased inventory", guild=GUILD_ID)
@app_commands.describe(member="The member whose inventory you want to see")
async def inventory(interaction: discord.Interaction, member: discord.Member):
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT s.name, s.description, i.purchased_at
        FROM user_inventory i
        JOIN store_items s ON i.item_id = s.id
        WHERE i.discord_id = ?
        ORDER BY i.purchased_at DESC
    """, (str(member.id),))
    items = cur.fetchall()
    conn.close()

    if not items:
        await interaction.response.send_message(f"{member.display_name} has no items in their inventory.", ephemeral=True)
        return

    embed = discord.Embed(title=f"{member.display_name}'s Inventory", color=discord.Color.blue())
    for name, desc, purchased_at in items:
        embed.add_field(name=name, value=f"{desc}\n*Purchased at:* {purchased_at}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@app_commands.checks.has_role("Admin")
@client.tree.command(name="removeuseritem", description="Remove the oldest instance of an item from a user's inventory", guild=GUILD_ID)
@app_commands.describe(member="The member whose inventory item to remove", item_name="Name of the item to remove")
async def removeuseritem(interaction: discord.Interaction, member: discord.Member, item_name: str):
    conn = sqlite3.connect("points.db")
    cur = conn.cursor()

    # Find the item ID from the store by name
    cur.execute("SELECT id FROM store_items WHERE name = ?", (item_name,))
    item = cur.fetchone()
    if not item:
        await interaction.response.send_message(f"Item **{item_name}** not found in store.", ephemeral=True)
        conn.close()
        return

    item_id = item[0]

    # Get the oldest (FIFO) inventory entry for this item and user
    cur.execute("""
        SELECT id
        FROM user_inventory
        WHERE discord_id = ?
          AND item_id = ?
        ORDER BY purchased_at ASC
        LIMIT 1
    """, (str(member.id), item_id))
    inventory_entry = cur.fetchone()

    if not inventory_entry:
        await interaction.response.send_message(f"{member.display_name} does not own any **{item_name}**.", ephemeral=True)
        conn.close()
        return

    # Delete the oldest inventory entry
    cur.execute("DELETE FROM user_inventory WHERE id = ?", (inventory_entry[0],))
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"Removed the oldest **{item_name}** from {member.display_name}'s inventory.", ephemeral=True
    )

@app_commands.checks.has_role("Admin")
@client.tree.command(name="ticketsetup", description="Post the ticket creation message", guild=GUILD_ID)
async def ticketsetup(interaction: discord.Interaction):
    ticket_channel_id = int(os.getenv("TICKET_PROMPT_CHANNEL_ID"))  # Add this to your .env
    ticket_channel = interaction.guild.get_channel(ticket_channel_id)

    if not ticket_channel:
        await interaction.response.send_message("Ticket channel not found.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Need Help or Want to Redeem Rewards?",
        description="React with 🎫 to open a support ticket.",
        color=discord.Color.blue()
    )
    message = await ticket_channel.send(embed=embed)
    await message.add_reaction("🎫")

    # Save the message ID
    set_setting("ticket_prompt_message_id", str(message.id))

    await interaction.response.send_message("Ticket message posted and reaction added.", ephemeral=True)

client.run(token)