import random

from flask import g

from bbgm import app
from bbgm.core import play_menu
from bbgm.util import get_payroll, lock
import bbgm.util.const as c

def new(player_id, resigning=False):
    """Start a new contract negotiation with player.

    Args: 
        player_id: An integer that must correspond with a free agent.
        resigning: A boolean. True if this is a negotiation for a contract
            extension with a current player who just became a free agent. False
            otherwise.

    Returns:
        False if the new negotiation is started successfully. Otherwise, it
        returns a string containing an error message to be sent to the user.
    """
    app.logger.debug('Trying to start new contract negotiation with player %d' % (player_id,))

    if resigning:
        resigning = True
    else:
        resigning = False

    if (g.phase >= c.PHASE_AFTER_TRADE_DEADLINE and g.phase <= c.PHASE_AFTER_DRAFT) and not resigning:
        return "You're not allowed to sign free agents now."
    r = g.dbex('SELECT COUNT(*) FROM player_attributes WHERE team_id = :team_id', team_id=g.user_team_id)
    num_players_on_roster, = r.fetchone()
    if num_players_on_roster >= 15 and not resigning:
        return "Your roster is full. Before you can sign a free agent, you'll have to buy out or release one of your current players.";
    if not lock.can_start_negotiation():
        return "You cannot initiate a new negotiaion while game simulation is in progress or a previous contract negotiation is in process."
    r = g.dbex('SELECT team_id FROM player_attributes WHERE player_id = :player_id', player_id = player_id)
    if r.rowcount:
        team_id, = r.fetchone()
        if team_id != c.PLAYER_FREE_AGENT:
            return "Player %d is not a free agent." % (player_id,)
    else:
        return "Player %d does not exist." % (player_id,)

    # Initial player proposal
    r = g.dbex('SELECT contract_amount*(1+free_agent_times_asked/10), contract_expiration FROM player_attributes WHERE player_id = :player_id', player_id = player_id)
    player_amount, expiration = r.fetchone()
    player_years = expiration - g.season
    # Adjust to account for in-season signings
    if g.phase <= c.PHASE_AFTER_TRADE_DEADLINE:
        player_years += 1

    max_offers = random.randint(1, 5)

    g.dbex('INSERT INTO negotiation (player_id, team_amount, team_years, player_amount, player_years, num_offers_made, max_offers, resigning) VALUES (:player_id, :player_amount, :player_years, :player_amount, :player_years, 0, :max_offers, :resigning)', player_id=player_id, player_amount=player_amount, player_years=player_years, max_offers=max_offers, resigning=resigning)
    lock.set_negotiation_in_progress(True)
    play_menu.set_status('Contract negotiation in progress...')
    play_menu.refresh_options()

    # Keep track of how many times negotiations happen with a player
    if not resigning:
        g.dbex('UPDATE player_attributes SET free_agent_times_asked = free_agent_times_asked + 1 WHERE player_id = :player_id', player_id = player_id)

    return False

def offer(player_id, team_amount, team_years):
    """Make an offer to a player.

    player_id must correspond with an ongoing negotiation.
    """
    app.logger.debug('User made contract offer for %d over %d years to %d' % (team_amount, team_years, player_id))

    if team_amount > 20000:
        team_amount = 20000
    if team_years > 5:
        team_years = 5
    if team_amount < 500:
        team_amount = 500
    if team_years < 1:
        team_years = 1

    r = g.dbex('SELECT player_amount, player_years, num_offers_made, max_offers FROM negotiation WHERE player_id = :player_id', player_id = player_id)
    player_amount, player_years, num_offers_made, max_offers = r.fetchone()

    num_offers_made += 1
    if num_offers_made <= max_offers:
        if team_years < player_years:
            player_years -= 1
            player_amount *= 1.2
        elif team_years > player_years:
            player_years += 1
            player_amount *= 1.2
        if team_amount < player_amount and team_amount > 0.7 * player_amount:
            player_amount = .75 * player_amount + .25 * team_amount
        elif team_amount < player_amount:
            player_amount *= 1.1
        if team_amount > player_amount:
            player_amount = team_amount
    else:
        player_amount = 1.05 * player_amount

    if player_amount > 20000:
        player_amount = 20000
    if player_years > 5:
        player_years = 5

    g.dbex('UPDATE negotiation SET team_amount = :team_amount, team_years = :team_years, player_amount = :player_amount, player_years = :player_years, num_offers_made = :num_offers_made WHERE player_id = :player_id', team_amount=team_amount, team_years=team_years, player_amount=player_amount, player_years=player_years, num_offers_made=num_offers_made, player_id=player_id)

def accept(player_id):
    """Accept the player's offer.

    player_id must correspond with an ongoing negotiation.

    Returns False if everything works. Otherwise, a string containing an error
    message (such as "over the salary cap") is returned.
    """
    app.logger.debug('User accepted contract proposal from %d' % (player_id,))

    r = g.dbex('SELECT player_amount, player_years, resigning FROM negotiation WHERE player_id = :player_id', player_id = player_id)
    player_amount, player_years, resigning = r.fetchone()

    # If this contract brings team over the salary cap, it's not a minimum
    # contract, and it's not resigning a current player, ERROR!
    payroll = get_payroll(g.user_team_id)
    if not resigning and (payroll + player_amount > g.salary_cap and player_amount != 500):
        return 'This contract would put you over the salary cap. You cannot go over the salary cap to sign free agents to contracts higher than the minimum salary. Either negotiate for a lower contract, buy out a player currently on your roster, or cancel the negotiation.'

    # Adjust to account for in-season signings
    if g.phase <= c.PHASE_AFTER_TRADE_DEADLINE:
        player_years -= 1

    r = g.dbex('SELECT MAX(roster_position) + 1 FROM player_attributes WHERE team_id = :team_id', team_id = g.user_team_id)
    roster_position, = r.fetchone()

    g.dbex('UPDATE player_attributes SET team_id = :team_id, contract_amount = :contract_amount, contract_expiration = :contract_expiration, roster_position = :roster_position WHERE player_id = :player_id', team_id=g.user_team_id, contract_amount=player_amount, contract_expiration=g.season + player_years, roster_position=roster_position, player_id=player_id)

    g.dbex('DELETE FROM negotiation WHERE player_id = :player_id', player_id = player_id)
    lock.set_negotiation_in_progress(False)
    play_menu.set_status('Idle')
    play_menu.refresh_options()

    return False

def cancel(player_id):
    """Cancel contract negotiations with a player.

    player_id must correspond with an ongoing negotiation.
    """
    app.logger.debug('User canceled contract negotiations with %d' % (player_id,))

    # Delete negotiation
    g.dbex('DELETE FROM negotiation WHERE player_id = :player_id', player_id = player_id)

    # If no negotiations are in progress, update status
    r = g.dbex('SELECT 1 FROM negotiation')
    if r.rowcount == 0:
        lock.set_negotiation_in_progress(False)
        play_menu.set_status('Idle')
        play_menu.refresh_options()

def cancel_all():
    """Cancel all ongoing contract negotiations.
    As of the time that I'm writing this, the only time there should be multiple
    ongoing negotiations in the first place is when a user is resigning players
    at the end of the season, although that should probably change eventually.
    """
    app.logger.debug('Canceling all ongoing contract negotiations...')

    # If no negotiations are in progress, update status
    r = g.dbex('SELECT player_id FROM negotiation')
    for player_id, in r.fetchall():
        cancel(player_id)
