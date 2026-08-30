"""Die englischen Texte der Schnittstelle - an einer Stelle.

⚠️ **Warum das eine eigene Datei ist und nicht am Dekorator steht.**

Jeder Docstring in Nexview ist deutsch. Das ist Absicht: Er begruendet eine
Entscheidung fuer den naechsten, der die Datei liest, und dieses Projekt wird
auf Deutsch geschrieben. Gleichzeitig stellt FastAPI genau diesen Docstring als
oeffentlichen Text auf ``/docs`` - und was nach draussen geht, ist englisch.

Beides zusammen geht nur, wenn der aeussere Text woanders steht als der innere.
Die naheliegende Loesung waere ``summary=``/``description=`` am Dekorator. Bei
dreizehn Adressen ist das richtig, und in ``routers/v1.py`` steht es auch genau
so - dort gehoert der Text neben die Zusage, gegen die geprueft wird.

Bei **214** Adressen ueber 30 Module ist es falsch:

- Jeder Router traegt dann zwei Erklaerungen je Funktion, eine deutsche und
  eine englische, und wird beim Lesen unbrauchbar.
- Niemand kann sagen, ob die Schnittstelle vollstaendig beschrieben ist, ohne
  30 Dateien durchzusehen.
- Der oeffentliche Text ist ein zusammenhaengendes Dokument. Ueber 30 Dateien
  verteilt schreibt ihn niemand in einem Zug, und er liest sich entsprechend.

Hier steht er als ein Dokument. Was auf ``/docs`` erscheint, laesst sich in
einer Datei lesen und aendern - und ``test_api_englisch.py`` prueft beide
Richtungen: keine Adresse ohne Text, kein Text ohne Adresse.

**Schluessel ist ``"METHODE /pfad"``** - genau wie in der Adresszeile, mit den
geschweiften Platzhaltern von FastAPI.
"""

from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.routing import APIRoute

#: Kurztitel und Beschreibung je Operation.
#:
#: Der Kurztitel steht in der Liste auf ``/docs`` und sollte ohne den Pfad
#: verstaendlich sein. Die Beschreibung darf Markdown enthalten und mehrere
#: Absaetze haben - sie ist das, was jemand liest, bevor er etwas anbindet.
TEXTE: dict[str, tuple[str, str]] = {
    # --- Rueckkanal (Webhooks) ---------------------------------------------
    'GET /api/settings/webhooks': (
        'Notification link status per instance',
        (
            'For every configured Radarr/Sonarr instance: whether the '
            'per-instance switch is on, whether the entry currently exists '
            'over there, when the reachability proof last arrived, when the '
            'last call came in - and, if the link cannot be established, an '
            'honest reason code.'
        ),
    ),
    'PATCH /api/settings/webhooks/{kennung}': (
        'Switch the notification link for one instance',
        (
            'Turning it on runs the proof and creates the entry in '
            'Radarr/Sonarr; turning it off removes that entry without '
            'leftovers. The response carries the resulting state.'
        ),
    ),
    'DELETE /api/settings/instanzen/{kennung}': (
        'Remove access to one instance',
        (
            'Clears the stored address, key, name and per-instance rules of '
            'one Radarr/Sonarr instance - nothing changes inside the instance '
            'itself, except that Nexview removes its own webhook entry first. '
            'Running requests of that instance stay put and simply stop '
            'updating.'
        ),
    ),
    'GET /api/settings/instanzen/verbindung': (
        'Reachability of the instances',
        (
            'Asks every configured Radarr/Sonarr instance for its status, all '
            'at once and with a short timeout - the live source of the status '
            'light on the instance tiles. Nothing is stored.'
        ),
    ),
    'GET /api/settings/instanzen/gesundheit': (
        'Health problems the instances report',
        (
            'The last seen /health state of every configured Radarr/Sonarr '
            'instance, refreshed each sync round. Messages are passed on in '
            'the instance’s own words.'
        ),
    ),
    'POST /api/settings/webhooks/{kennung}/testen': (
        'Prove the notification link right now',
        (
            'Asks the instance to send its test event to Nexview and reports '
            'whether - and how fast - the call arrived, or why it could not.'
        ),
    ),
    'POST /api/webhooks/arr/{kennung}': (
        'Inbound call from Radarr or Sonarr',
        (
            'Receiving end of the notification entry Nexview maintains inside '
            'Radarr and Sonarr. Expects the per-instance secret as the Basic '
            'auth password. The call only wakes the status sync - its payload '
            'is never trusted. A "Test" event records the reachability proof '
            'instead of waking anything.'
        ),
    ),
    # --- Speicher und Statistik --------------------------------------------
    'GET /api/admin/stats': (
        'Numbers for the dashboard',
        (
            'Counts for requests, downloads, quotas and ratings across the '
            'installation.'
        ),
    ),
    'GET /api/admin/stats/aufraeumen': (
        'What nobody watches any more',
        (
            'Titles in the whole library that nobody has watched for a long time, '
            'largest first - **including the house collection**. Two clocks decide, not '
            'one: an item appears only if nobody watched it in the chosen period *and* '
            'it has been here at least that long. Without the second clock the 60 GB '
            'file that arrived yesterday would top the list before anyone had a chance '
            'to watch it. **Administrators only**, unlike the rest of the statistics: '
            "acting on this list is an administrator's job anyway, and the rows name "
            'who last watched what.'
        ),
    ),
    'POST /api/storage/abgleich': (
        'Measure storage now',
        (
            'Run the storage measurement immediately instead of waiting for the hourly '
            'pass. Built for one moment in particular: the first start after an update. '
            'The cleanup suggestion needs the file date from Radarr/Sonarr, and until '
            'the first pass has run there is nothing to suggest from.'
        ),
    ),
    'POST /api/storage/entries/{posten_id}/abgeben': (
        'Offer an item up',
        (
            '"I do not need this any more" - puts one of your own items up for a '
            'decision. **Nothing happens to the file**, and the item **still counts** '
            'against your quota until somebody decides. Anything else would let people '
            'free their quota by offering everything up and never following through.'
        ),
    ),
    'POST /api/storage/entries/{posten_id}/behalten': (
        'Withdraw an offer',
        (
            'Take back an offer - "actually, no". Stops an accidental click from '
            'standing until the next decision. Nothing changes on the quota: the item '
            'counted the whole time.'
        ),
    ),
    'GET /api/storage/entries/{posten_id}/dateien': (
        'Which files would go',
        (
            'The dry run: exactly which files a deletion would remove. **Nothing is '
            'touched.** An administrator confirms later with *this list* in front of '
            'them rather than with a number - a mistake when deleting season by season '
            'is expensive, and a count does not show which season it would hit.'
        ),
    ),
    'POST /api/storage/entries/{posten_id}/entfolgen': (
        'Keep it, stop following',
        (
            'Carries out the third possible outcome of an offer: the files stay, the '
            'monitoring stops. It is the only outcome where the item **stays charged** '
            'to its owner - the files are still there, and somebody wanted them kept.'
        ),
    ),
    'POST /api/storage/entries/{posten_id}/haus': (
        'Move an item to the house',
        (
            "Attribute an item to the house instead of a person, freeing that person's "
            'quota. **No file is touched.** The title stays exactly where it is; only '
            'who it is charged to changes. That is the point: an administrator can '
            'relieve someone without anybody losing anything.'
        ),
    ),
    'POST /api/storage/entries/{posten_id}/loeschen': (
        'Delete a title and its files',
        (
            'Removes the title **including the file**. ⚠️ **The one operation in '
            'Nexview with no way back**, apart from whatever recycle bin Radarr or '
            'Sonarr provide. If none is configured, the file is gone immediately and '
            'for good - so the caller is expected to have shown the file list first.'
        ),
    ),
    'POST /api/storage/entries/{posten_id}/vormerken': (
        'Mark for deletion, with a grace period',
        (
            'Sets a deletion date in the future instead of deleting now. **Nothing '
            'happens to the file**: the item stays where it is, keeps counting, and can '
            'be rescued until the last minute. Anyone who watches it in the meantime '
            'cancels the deletion without ever learning one was pending.'
        ),
    ),
    'POST /api/storage/entries/{posten_id}/vormerkung-aufheben': (
        'Cancel a pending deletion',
        (
            'Takes the deletion mark off again. Everyone who was told about the '
            'deletion is told about this too - an announcement only the person who made '
            'it can see is not an announcement.'
        ),
    ),
    'GET /api/storage/house': (
        'What the house holds',
        (
            'Everything attributed to the house, largest first, paged and searchable. '
            '**Administrators only, for a concrete reason:** the rows carry the path on '
            'the server, and an ordinary account has no business knowing the layout of '
            'the machine.'
        ),
    ),
    'GET /api/storage/me': (
        'Your own storage',
        (
            'What is attributed to the calling account, largest first, with the '
            'individual items. The order is the point of the list: anyone asked to free '
            'up space needs to see where the space actually is. **Without paths** - '
            "where a file sits on the server is none of a requester's business."
        ),
    ),
    'GET /api/storage/me/aufraeumen': (
        'Your own unwatched titles',
        (
            'What is attributed to this account and has not been watched for a long '
            'time. The same service as the statistics view, but narrowed - and '
            '**without the house collection**, because you can only give up what is '
            'yours.'
        ),
    ),
    'GET /api/storage/overview': (
        'Who uses how much',
        (
            'Storage use per account. Deliberately **administrators only**: how much '
            'space somebody takes up is a statement about a person. Approvers do not '
            'see it, for the same reason they do not see the ticket centre.'
        ),
    ),
    'GET /api/storage/recyclebin': (
        'What sits in the recycle bins',
        (
            'What Radarr and Sonarr are holding in their recycle bins - and therefore '
            'still occupying disk. **A recycle bin is not free space.** What sits there '
            'has not left the disk; it is only waiting for its retention period to run '
            'out.'
        ),
    ),
    'GET /api/storage/releases': (
        'Items awaiting a decision',
        (
            'Offers waiting to be decided, oldest first. **Administrators only, and '
            'that is a safety rule:** approvers have a quota of their own *and* '
            'standing auto-approval. Letting them decide offers would let them shift '
            'their own storage onto the house.'
        ),
    ),
    'GET /api/storage/umbuchung': (
        'Preview: move everything to the house',
        (
            'The numbers behind "move all holdings to the house". The dialogue in front '
            'of it has to be able to say "X titles totalling Y GB will become house '
            'holdings" - a general warning gets clicked away, a number gets read.'
        ),
    ),
    'POST /api/storage/umbuchung': (
        'Move everything to the house',
        (
            'Attributes every item to the house, so every account starts at zero. Until '
            '0.19 this happened as a **side effect** of switching the accounting mode. '
            'The mode is gone; the operation remains, as the deliberate way to reset a '
            'household that has grown lopsided.'
        ),
    ),
    'GET /api/storage/user/{user_id}': (
        'Storage of one account',
        (
            'What this one account occupies, largest first. **Administrators only**, '
            'for the same reason as the overview: how much space somebody takes up is a '
            'statement about a person.'
        ),
    ),
    'GET /api/storage/vorgemerkt': (
        'What is about to disappear',
        (
            'Everything marked for deletion, **visible to everyone**. Deliberately not '
            'restricted to administrators: the whole purpose of a grace period is that '
            'the household notices it. An announcement only its author reads is not an '
            'announcement.'
        ),
    ),
    # --- Anmeldung, Konten, Einrichtung ------------------------------------
    'POST /api/auth/login': (
        'Sign in',
        (
            'Exchanges username or e-mail and password for an access token. The refresh '
            'token comes back as an HttpOnly cookie rather than in the body, so no '
            'script in the page can read it. Repeated failures slow the endpoint down '
            'deliberately.'
        ),
    ),
    'POST /api/auth/logout': (
        'Sign out of this browser',
        (
            'Clears the cookie. **Without an authentication check, on purpose:** anyone '
            'who wants to sign out should be able to, even if their access token '
            'expired long ago. Note this ends the session in *this* browser only - to '
            'end them everywhere, see the sign-out-everywhere endpoint.'
        ),
    ),
    'GET /api/auth/me': (
        'Your own account',
        (
            'Everything the calling account knows about itself: name, role, language, '
            'theme, quota settings and linked media-server accounts.'
        ),
    ),
    'PATCH /api/auth/me': (
        'Change your own account',
        (
            'Display name, language, region, theme and notification preferences. The '
            'username is not among them: it appears in requests, approvals and the '
            'Radarr/Sonarr labels, and changing it afterwards would tear those traces '
            'apart.'
        ),
    ),
    'DELETE /api/auth/me/avatar': (
        'Remove your profile picture',
        'Deletes the picture file and falls back to the generated initials.',
    ),
    'POST /api/auth/me/avatar': (
        'Upload a profile picture',
        (
            'Accepts PNG, JPEG, GIF and WebP. The image is scaled down and re-encoded '
            'on the server, so what ends up stored is not the file that was sent.'
        ),
    ),
    'PUT /api/auth/me/email': (
        'Change your own e-mail address',
        (
            'The new address only counts as confirmed once the link in the mail has '
            "been clicked - otherwise anyone could enter somebody else's address. Until "
            'then the old one stays in force for password resets.'
        ),
    ),
    'POST /api/auth/me/password': (
        'Change your own password',
        (
            'Changes the password and signs out every **other** device. Since 0.21 any '
            'token older than `password_changed_at` is refused, so a copy somebody made '
            'of your session stops working the moment you change it.'
        ),
    ),
    'POST /api/auth/me/resend-verification': (
        'Send the confirmation mail again',
        (
            'Requests a fresh confirmation mail for an address that has not been '
            'confirmed yet.'
        ),
    ),
    'GET /api/auth/me/schluessel': (
        'Your own API tokens',
        (
            'Lists the tokens belonging to the calling account - name, preview, whether '
            'it may only read, when it expires and when it was last used. **Never the '
            'token itself**; that exists once, at creation.'
        ),
    ),
    'POST /api/auth/me/schluessel': (
        'Create an API token',
        (
            '⚠️ **The plain text appears in this one response and nowhere else.** '
            'Afterwards only a checksum remains, and not even an administrator can look '
            'it up. A token inherits the rights of the account it belongs to; '
            '`nur_lesen` restricts it to GET requests. Child accounts cannot create '
            'tokens.'
        ),
    ),
    'DELETE /api/auth/me/schluessel/{schluessel_id}': (
        'Revoke one of your tokens',
        (
            'Takes effect immediately - anything using that token is locked out from '
            'the next request onwards. You can only revoke your own; an administrator '
            'can see foreign tokens but not switch them off.'
        ),
    ),
    'POST /api/auth/me/ueberall-abmelden': (
        'Sign out everywhere',
        (
            'Ends every session of this account on every device, including the one '
            'making the call, without changing the password. ⚠️ **The way out that did '
            'not exist before 0.22.** Ordinary sign-out only removes the cookie from '
            '*this* browser; a copy taken elsewhere kept working until it expired, up '
            'to 30 days.'
        ),
    ),
    'POST /api/auth/refresh': (
        'Renew the access token',
        (
            'The refresh token is read from the HttpOnly cookie, not from the request '
            'body. Clients still holding a token in `localStorage` from an older '
            'version have to sign in once.'
        ),
    ),
    'POST /api/onboarding/forgot-password': (
        'Request a password reset link',
        (
            'The response is **always the same**, whether the address exists or not. '
            'Otherwise anyone could probe here for who has an account - and on a '
            'private installation, that is exactly the list nobody should be able to '
            'build.'
        ),
    ),
    'GET /api/onboarding/invitation/{raw}': (
        'Check an invitation link',
        (
            'Says whether the link is still valid and what it offers, without signing '
            'anybody in. Used by the page behind the link to decide between a form and '
            'an explanation.'
        ),
    ),
    'POST /api/onboarding/invitation/{raw}': (
        'Redeem an invitation',
        (
            'Creates the account the way the invited person wants it - username, '
            'password, display name. The account does not exist until this call '
            'succeeds.'
        ),
    ),
    'GET /api/onboarding/password/{raw}': (
        'Check a password link',
        (
            'Says whether a reset or first-password link is still valid, so the page '
            'can show a form or an explanation instead of failing on submit.'
        ),
    ),
    'POST /api/onboarding/password/{raw}': (
        'Set a password',
        (
            'First password or a new one. The address counts as confirmed afterwards - '
            'the mail evidently arrived. Every existing session becomes invalid: anyone '
            'resetting a password has a reason to assume somebody else was in.'
        ),
    ),
    'PUT /api/onboarding/pending/email': (
        'Correct an unconfirmed address',
        (
            'The most common reason for a mail that never arrives is a typo. Without '
            'this route the installation would be stuck: no confirmed address, no '
            'sign-in, and no way to fix the address.'
        ),
    ),
    'POST /api/onboarding/pending/resend': (
        'Send the confirmation mail again, without signing in',
        (
            'The counterpart to the signed-in version, for an account that cannot sign '
            'in yet because its address is unconfirmed.'
        ),
    ),
    'GET /api/onboarding/username-available': (
        'Is this username still free',
        (
            'Answers while somebody is still typing. Deliberately open without '
            'authentication: whoever is redeeming an invitation is nobody yet. It '
            'reveals only whether a name is taken - the same thing the form would '
            'reveal on submit.'
        ),
    ),
    'POST /api/onboarding/verify/{raw}': (
        'Confirm an e-mail address',
        'Marks the address behind the link as confirmed.',
    ),
    'POST /api/setup/admin': (
        'Create the first administrator',
        (
            'Only works while the installation is still empty. Afterwards accounts come '
            'into being through invitations.'
        ),
    ),
    'POST /api/setup/sicherung/einspielen': (
        'Set up from a backup',
        (
            'Builds the fresh installation from a backup file. Setup is finished '
            'afterwards: the accounts come from the backup, and the wizard does not '
            'appear again.'
        ),
    ),
    'POST /api/setup/sicherung/pruefen': (
        'Inspect a backup file',
        (
            'Reports what a backup contains and whether this version can restore it - '
            '**without replacing anything**. A backup from a newer version is refused, '
            'because the database can only be migrated forward.'
        ),
    ),
    'GET /api/setup/status': (
        'Is setup still needed',
        (
            'Says whether this installation has been set up yet, and which step it '
            'stopped at. Answers without authentication - there is nobody to '
            'authenticate as.'
        ),
    ),
    'GET /api/users': (
        'All accounts',
        'Every account with its role, quota and current usage. Administrators only.',
    ),
    'GET /api/users/api-schluessel': (
        'All API tokens in this installation',
        (
            'Who holds which token, when it was made and when it was last used. **Never '
            'the token itself** - an administrator can supervise, not read. Revoking is '
            'not offered here: only the owner can switch off their own token.'
        ),
    ),
    'GET /api/users/invitations': (
        'Open invitations',
        (
            'Invitations that have neither been redeemed nor expired. Used-up ones are '
            'of no further interest.'
        ),
    ),
    'POST /api/users/invitations': (
        'Invite somebody',
        (
            'The account comes into being when the link is redeemed, not now. Needs '
            'both pieces in place: without a public address the mail contains a dead '
            'link, without a mail server it never goes out.'
        ),
    ),
    'DELETE /api/users/invitations/{invitation_id}': (
        'Withdraw an invitation',
        'The link stops working immediately.',
    ),
    'DELETE /api/users/{user_id}': (
        'Delete an account',
        (
            'Approved requests move into the house collection - they stay, just without '
            'an owner. Only what is still open gets cancelled. Use the preview first to '
            'see exactly what will happen.'
        ),
    ),
    'PATCH /api/users/{user_id}': (
        'Change an account',
        (
            'Role, quota, auto-approval, active state and display name. Administrators '
            'only.'
        ),
    ),
    'GET /api/users/{user_id}/aufloesung': (
        'Preview: what deleting would leave behind',
        (
            'What this account would leave behind - **without anything happening**. The '
            'administrator decides with this list in front of them: per item house or '
            'delete, per open request keep or cancel.'
        ),
    ),
    'POST /api/users/{user_id}/password': (
        "Set an account's password",
        (
            'An administrator sets a new password directly, for the case where mail is '
            'not working and the reset link cannot arrive.'
        ),
    ),
    'POST /api/users/{user_id}/quota/reset': (
        'Reset the request count',
        (
            'Sets usage in the current period back to zero. The requests themselves '
            'stay - counting simply starts again from now. At the next period change '
            'the calendar takes over as usual.'
        ),
    ),
    'POST /api/users/{user_id}/storage/reset': (
        "Move an account's storage to the house",
        (
            'The counterpart to resetting the request count, and the way out of a '
            '**ghost item**: something requested through Nexview that no longer exists '
            'in Radarr or Sonarr keeps counting against its owner forever otherwise.'
        ),
    ),
    # --- Kinderkonten ------------------------------------------------------
    'GET /api/children': (
        'Your own child accounts',
        (
            'The child profiles belonging to the calling account. Anyone without '
            'children gets an empty list. Child accounts are not accounts of their own: '
            'they are sub-profiles of a parent, and everything they cause runs against '
            "the parent's quota."
        ),
    ),
    'POST /api/children': (
        'Create a child profile',
        (
            'Needs permission from an administrator first. The child gets its own '
            'sign-in and its own set of enabled categories, but no media-server link '
            'and no quota of its own.'
        ),
    ),
    'GET /api/children/genres': (
        'The categories a child can be given',
        (
            'The selectable categories in their fixed order. The interface fetches them '
            'here rather than listing them itself - otherwise there would be two lists '
            'that drift apart.'
        ),
    ),
    'POST /api/children/request-permission': (
        'Ask to be allowed child profiles',
        (
            'A button, not a form: the text is fixed, so nobody has to invent one. '
            'Lands as a ticket with the administrators.'
        ),
    ),
    'GET /api/children/wishes': (
        'Wishes waiting for your decision',
        (
            'What the children of the calling account have wished for and nobody has '
            'decided yet.'
        ),
    ),
    'POST /api/children/wishes/{wish_id}/decline': (
        'Turn down a wish',
        'The child is told, and the wish is closed. Nothing is requested.',
    ),
    'POST /api/children/wishes/{wish_id}/release': (
        'Turn a wish into a request',
        (
            "The wish becomes a request **in the parent's name**. The title is fetched "
            'from *their* point of view: it is their request, against their quota and '
            'their age settings.'
        ),
    ),
    'DELETE /api/children/{child_id}': (
        'Delete a child profile',
        (
            "Removes the profile and its sign-in. Requests already made in the parent's "
            "name are unaffected - they were never the child's."
        ),
    ),
    'PATCH /api/children/{child_id}': (
        'Change a child profile',
        'Display name, enabled categories and age limit.',
    ),
    'POST /api/children/{child_id}/password': (
        "Set a child's password",
        (
            'The parent sets it directly. A child has no e-mail address, so there is no '
            'reset link to send.'
        ),
    ),
    'GET /api/children/{child_id}/preview/backdrops': (
        "Preview: the child's backdrops",
        (
            'What the child sees behind its start page. Part of the preview, so a '
            'parent can check what they have enabled without signing in as the child.'
        ),
    ),
    'GET /api/children/{child_id}/preview/categories': (
        "Preview: the child's start page",
        (
            'The start page exactly as this child would see it - one tile per enabled '
            'category.'
        ),
    ),
    'GET /api/children/{child_id}/preview/rubrik/{rubrik}': (
        'Preview: one category',
        (
            'Everything in one category, as the child would see it. The category is '
            'checked against what this child is allowed.'
        ),
    ),
    'GET /api/children/{child_id}/preview/search': (
        "Preview: the child's search",
        (
            "Searches within the child's enabled categories only, so a parent can see "
            'what a search would turn up.'
        ),
    ),
    'GET /api/children/{child_id}/preview/title/{media_type}/{tmdb_id}': (
        'Preview: one title',
        (
            'A single title exactly as the child would see it, including the category '
            'check.'
        ),
    ),
    'GET /api/kids/backdrops': (
        'Backdrops for the start page',
        "Images for the background of the children's view.",
    ),
    'GET /api/kids/categories': (
        "The children's start page",
        (
            'One tile per enabled category. Categories first, titles second - so a '
            'child sees what things are about instead of walking into a wall of '
            'posters.'
        ),
    ),
    'GET /api/kids/rubrik/{rubrik}': (
        'Everything in one category',
        (
            'The whole category at once rather than a sideways-scrolling row. ⚠️ The '
            'category is checked: otherwise one that was never enabled could be '
            'requested through the address bar.'
        ),
    ),
    'GET /api/kids/search': (
        'Search, within the enabled categories only',
        (
            'The restriction sits in the service, not in the interface. If nothing is '
            'found, the child is told so plainly rather than being shown an empty page.'
        ),
    ),
    'GET /api/kids/title/{media_type}/{tmdb_id}': (
        'One title for a child',
        (
            'A single title with its trailer and nothing else. ⚠️ The category is '
            'checked **here** too, not only when listing: otherwise a blocked title '
            'could be reached straight through the address bar.'
        ),
    ),
    'GET /api/kids/wishes': (
        "The child's own wishes",
        'What this child has wished for and what became of each wish.',
    ),
    'POST /api/kids/wishes': (
        'Wish for something',
        (
            "The title is fetched from TMDB **again** here, with this child's settings: "
            'a blocked title fails the age check at that point rather than after the '
            'parent has already seen it.'
        ),
    ),
    # --- Medienserver ------------------------------------------------------
    'GET /api/admin/mediaserver/blocks': (
        'Blocked media-server accounts',
        (
            'Media-server accounts that are barred from signing in to Nexview. '
            'Administrators only.'
        ),
    ),
    'DELETE /api/admin/mediaserver/blocks/{block_id}': (
        'Lift a block',
        'This media-server account may sign in again afterwards.',
    ),
    'POST /api/admin/mediaserver/connect/password': (
        'Connect a server with username and password',
        (
            'For servers that offer no broker flow. The counterpart to '
            'start/poll/select, where somebody confirms in a browser window instead.'
        ),
    ),
    'POST /api/admin/mediaserver/connect/poll': (
        'Check whether the sign-in was confirmed',
        (
            'Once confirmed, returns the servers to choose from. The operation stays '
            "open on purpose: the provider's token is held by the broker until a server "
            'has actually been selected.'
        ),
    ),
    'POST /api/admin/mediaserver/connect/select': (
        'Take a server into use',
        (
            'Adopts the chosen server and links the calling account to it in the same '
            'step.'
        ),
    ),
    'POST /api/admin/mediaserver/connect/start': (
        'Start signing in with the provider',
        (
            'Begins the broker flow. No server is chosen yet - that happens after '
            'confirmation.'
        ),
    ),
    'DELETE /api/admin/mediaserver/connection': (
        'Disconnect the media server',
        (
            "The users' links stay in place: reconnect the same server later and "
            'everything is still there. Use the consequences endpoint first to see who '
            'this would affect.'
        ),
    ),
    'GET /api/admin/mediaserver/connection/folgen': (
        'Who a disconnect would affect',
        (
            'Who would be affected - **before** the click, not after. Anyone who signs '
            'in through the media server and has no password of their own would be '
            'locked out.'
        ),
    ),
    'GET /api/admin/mediaserver/library': (
        'State of the library comparison',
        (
            'Per provider when one is named. The card sits on the page of *one* server; '
            'without `provider` it would show a total that belongs to no server in '
            'particular.'
        ),
    ),
    'POST /api/admin/mediaserver/library/refresh': (
        'Compare the library now',
        (
            'In normal operation this happens in the background. The button is for the '
            'moment right after connecting - and so there is any way at all to see '
            'whether the connection works.'
        ),
    ),
    'DELETE /api/auth/mediaserver/link': (
        'Unlink your own media-server account',
        (
            '`provider` unlinks exactly that provider. Without it, all of them go - the '
            'way it worked when there was only one.'
        ),
    ),
    'POST /api/auth/mediaserver/link/password': (
        'Link a media-server account with a password',
        (
            'Attaches a media-server account to the Nexview account you are already '
            'signed in to.'
        ),
    ),
    'POST /api/auth/mediaserver/link/poll': (
        'Check whether the link was confirmed',
        (
            "Polls the broker; once the user has confirmed in the provider's window, "
            'the link is made.'
        ),
    ),
    'POST /api/auth/mediaserver/link/start': (
        'Start linking a media-server account',
        (
            'Opens the broker flow for attaching a media-server account to the account '
            'you are signed in to.'
        ),
    ),
    'POST /api/auth/mediaserver/login/password': (
        'Sign in with media-server credentials',
        'Signs in to Nexview using the username and password of the media server.',
    ),
    'POST /api/auth/mediaserver/login/poll': (
        'Check whether the sign-in was confirmed',
        (
            'Polls the broker; once confirmed, returns Nexview tokens for the linked '
            'account.'
        ),
    ),
    'POST /api/auth/mediaserver/login/start': (
        'Start signing in via the media server',
        (
            'Opens the broker flow for signing in to Nexview with a media-server '
            'account.'
        ),
    ),
    # --- Anmeldung ueber fremde Anbieter (OIDC) -----------------------------
    'GET /api/auth/oidc': (
        'List the sign-in providers',
        (
            'The OpenID Connect providers the administrator has configured and '
            'enabled - slug and button label only, nothing sensitive. The login '
            'page uses this to decide which buttons to show; an empty list means '
            'the login page looks exactly as it always did.'
        ),
    ),
    'GET /api/auth/oidc/{slug}/login': (
        'Sign in via an OIDC provider',
        (
            'Redirects the browser to the configured provider (authorization '
            'code flow with PKCE). Meant to be navigated to, not called: the '
            'result is a redirect chain that ends back at the Nexview login '
            'page. Returns 404 for unknown or disabled providers.'
        ),
    ),
    'GET /api/auth/oidc/{slug}/callback': (
        'Return leg of the OIDC flow',
        (
            'The redirect URI registered with the provider. Verifies state, '
            'exchanges the code, validates the id_token and either starts a '
            'regular Nexview session (sign-in) or attaches the identity to the '
            'signed-in account (linking). Every outcome - including every '
            'error - is a redirect to the Nexview UI with a code in the query, '
            'never a bare API response.'
        ),
    ),
    'POST /api/auth/oidc/{slug}/link/start': (
        'Link an OIDC identity to the own account',
        (
            'Returns the provider URL to navigate to and arms the short-lived '
            'state cookie. A POST with a session rather than a redirect, '
            'because a browser navigation cannot carry the Authorization '
            'header.'
        ),
    ),
    'DELETE /api/auth/oidc/{slug}/link': (
        'Unlink an OIDC identity',
        (
            'Removes the own link to this provider. Refused (409) if the '
            'account would be left without any way back in - no password, no '
            'verified address, no other linked sign-in.'
        ),
    ),
    'GET /api/admin/oidc': (
        'List the configured OIDC providers',
        (
            'Every configured provider with its settings, the redirect URI to '
            'register on the provider side and how many accounts are linked. '
            'The client secret never leaves the database - the list only says '
            'whether one is set.'
        ),
    ),
    'POST /api/admin/oidc': (
        'Add an OIDC provider',
        (
            'Issuer URL, client id and secret, button label and the per-provider '
            'switches (enabled, auto-create). Refused while no public address is '
            'configured, because the redirect URI is built from it. The slug is '
            'fixed after creation - it is part of the redirect URI registered '
            'with the provider.'
        ),
    ),
    'PATCH /api/admin/oidc/{provider_id}': (
        'Change an OIDC provider',
        (
            'Everything but the slug. An empty client secret means "keep the '
            'stored one". Changing the issuer URL is allowed but means a '
            'different provider: existing links keep pointing at the old '
            'issuer.'
        ),
    ),
    'GET /api/admin/oidc/{provider_id}/folgen': (
        'Impact of deleting an OIDC provider',
        (
            'How many accounts are linked, and which of them would lose their '
            'only way in. Always available, so the answer is usually "nobody" - '
            'a warning that only ever appears in the bad case is never read.'
        ),
    ),
    'DELETE /api/admin/oidc/{provider_id}': (
        'Delete an OIDC provider',
        (
            'Removes the entry; the links of the users stay, so re-adding the '
            'same issuer later finds everything in place. Refused (409) with '
            'the list of endangered accounts if some would lose their only way '
            'in; can be overridden with bestaetigt=true.'
        ),
    ),
    'POST /api/admin/oidc/{provider_id}/pruefen': (
        'Probe an OIDC provider',
        (
            'Fetches the provider description fresh, bypassing the cache, and '
            'reports reachability as a structured result rather than an error. '
            'Whether client id and secret are right can only be proven by a '
            'real sign-in.'
        ),
    ),
    # --- Betrieb: Einstellungen, Kanaele, Protokoll, Sicherungen -----------
    'GET /api/about': (
        'Version and build',
        (
            'Which version of Nexview this is, and whether a newer one is known. Useful '
            'for telling deployments apart and for deciding whether a feature you rely '
            'on exists yet.'
        ),
    ),
    'POST /api/about/check': (
        'Check for updates now',
        (
            'Looks for a newer release immediately instead of waiting for the daily '
            'check.'
        ),
    ),
    'GET /api/about/neuigkeiten': (
        'Is there something new to read',
        (
            'Whether this installation has been updated since the administrator last '
            'acknowledged the release notes, and to which version.'
        ),
    ),
    'POST /api/about/neuigkeiten/gesehen': (
        'Acknowledge the release notes',
        (
            '"Understood, stop showing this" - until the next update. What is stored is '
            'the version, not a tick, so the banner comes back by itself after the next '
            'release that has something to say.'
        ),
    ),
    'GET /api/config': (
        'Public configuration',
        (
            'What the interface needs to know before anybody signs in: which '
            'media-server providers exist, whether watchlists are enabled, the minimum '
            'password length, the default region.'
        ),
    ),
    'GET /api/config/regions': (
        'Selectable regions',
        (
            'The countries somebody can pick as their region. Deliberately fetched from '
            'TMDB rather than kept in the source: a fixed list goes stale and silently '
            'limits who can use this.'
        ),
    ),
    'GET /api/settings': (
        'All settings',
        (
            'The whole configuration of this installation. Secrets come back masked, '
            'never in the clear. Administrators only.'
        ),
    ),
    'PUT /api/settings': (
        'Change settings',
        (
            '⚠️ **An empty secret field means "unchanged", not "delete".** Otherwise '
            'the masked value from the interface would be written back over the real '
            'one. Use the delete endpoint to actually remove a key.'
        ),
    ),
    'GET /api/settings/recyclebin': (
        'Where deleted files go',
        (
            'The recycle-bin path configured in every Radarr and Sonarr instance. '
            '**Fetched fresh on every call and stored nowhere** - it lives over there, '
            'and a copy here would go stale without anybody noticing.'
        ),
    ),
    'PUT /api/settings/recyclebin': (
        'Set the recycle-bin path',
        (
            '⚠️ **This writes into Radarr and Sonarr, not into Nexview.** The setting '
            'applies over there to **everything**, including deletions that had nothing '
            'to do with Nexview.'
        ),
    ),
    'GET /api/settings/recyclebin/contents': (
        'What is in the recycle bins',
        (
            '**Folder names only** - no posters, no tidied-up titles. Resolving the '
            'names back to titles would mean a TMDB lookup per folder for something '
            'that is, in the end, a list of directories.'
        ),
    ),
    'GET /api/settings/recyclebin/folders': (
        'Which folders one instance sees',
        (
            'Asked per instance rather than once for all: Sonarr can be mounted '
            'entirely differently from Radarr, and a path that exists for one may not '
            'exist for the other.'
        ),
    ),
    'DELETE /api/settings/secret/{name}': (
        'Remove a stored secret',
        (
            'The explicit way to delete an API key, because an empty field on save '
            'means "unchanged".'
        ),
    ),
    'POST /api/settings/test-mail': (
        'Send a test mail',
        (
            'Sends a fully formatted test message to the given address, so the result '
            'shows what a real notification will look like.'
        ),
    ),
    'POST /api/settings/test/public-url': (
        'Is Nexview reachable at this address',
        (
            'The server calls itself from the outside to find out. Deliberately with '
            'its own short-lived client, so nothing from the normal connection pool can '
            'make an unreachable address look reachable.'
        ),
    ),
    'POST /api/settings/test/smtp': (
        'Test the mail server',
        'Checks connection and authentication without sending anything.',
    ),
    'POST /api/settings/test/tmdb': (
        'Test the TMDB key',
        'Checks whether the given TMDB API key works.',
    ),
    'POST /api/settings/test/{service}': (
        'Test Radarr or Sonarr',
        (
            'Checks the connection to the standard or 4K instance. Uses the values '
            'passed in if they have not been saved yet, so a connection can be tested '
            'before it is stored.'
        ),
    ),
    'POST /api/settings/channels/{channel}/chats': (
        'Who wrote to this bot',
        (
            'For a bot that does not exist in the database yet - during setup the token '
            'is in the form, not stored. Saves the detour of asking a third-party bot '
            'who has written to yours.'
        ),
    ),
    'POST /api/settings/channels/{channel}/check': (
        'Is the instance reachable',
        (
            'Without a code - nothing can arrive there yet. For the upper level of '
            'services that have two: a typo in the address should show up before '
            'anybody sets up topics underneath it.'
        ),
    ),
    'POST /api/settings/channels/{channel}/confirm': (
        'Confirm the code from the test message',
        (
            'Only after this can the target be saved. Proves that messages actually '
            'arrive where they are supposed to.'
        ),
    ),
    'GET /api/settings/channels/{channel}/targets': (
        'All targets of this channel',
        'Every configured target of this service - one card each, topics included.',
    ),
    'POST /api/settings/channels/{channel}/targets': (
        'Create a target',
        'Creates an instance - with Gotify, that is also the mailbox.',
    ),
    'POST /api/settings/channels/{channel}/targets/{parent_id}/children': (
        'Create a topic',
        'Creates a topic inside an existing instance.',
    ),
    'DELETE /api/settings/channels/{channel}/targets/{target_id}': (
        'Remove a target',
        (
            'Removes the target and everything hanging off it. With an ntfy instance '
            'the topics go too - without an address and credentials they would have '
            'nothing to talk to.'
        ),
    ),
    'PUT /api/settings/channels/{channel}/targets/{target_id}': (
        'Change a target',
        (
            'Address, credentials and name. Changing what messages arrive there is a '
            'separate endpoint.'
        ),
    ),
    'POST /api/settings/channels/{channel}/targets/{target_id}/chats': (
        'Who wrote to this stored bot',
        (
            'The same question for a bot that is already saved: whoever sent it /start '
            'or added it to a group appears here, so the chat can be picked instead of '
            'typed.'
        ),
    ),
    'PUT /api/settings/channels/{channel}/targets/{target_id}/enabled': (
        'Switch a target on or off',
        (
            'Deliberately its own endpoint rather than part of saving: the switch sits '
            'on the card and should take effect where it is, without opening a form.'
        ),
    ),
    'PUT /api/settings/channels/{channel}/targets/{target_id}/events': (
        'What this mailbox is told about',
        (
            'Only on a confirmed target. Ticks on an unverified one would quietly fill '
            'the outbox with messages nobody receives.'
        ),
    ),
    'POST /api/settings/channels/{channel}/test': (
        'Send a test message with a code',
        (
            'Tests the **typed-in** values, not the stored ones - test first, save '
            'afterwards. The code has to be confirmed before the target counts as '
            'working.'
        ),
    ),
    'GET /api/logs': (
        'The most recent log lines',
        (
            '`level` means "this level and above". Administrators only - log lines name '
            'accounts and addresses.'
        ),
    ),
    'DELETE /api/logs': (
        'Clear the log',
        'Empties the log file. What is gone is gone; there is no second copy.',
    ),
    'GET /api/logs/level': (
        'Current log level',
        (
            'Which level is being written, and until when - the more talkative levels '
            'switch themselves off again.'
        ),
    ),
    'PUT /api/logs/level': (
        'Change the log level',
        (
            'Takes effect immediately, without a restart. The talkative levels carry an '
            'expiry so a debugging session cannot quietly fill the disk for weeks.'
        ),
    ),
    'GET /api/admin/sicherungen': (
        'All backups',
        (
            'What backups exist, how big they are, when they were made, whether '
            'automatic or manual, and whether this version could restore each one.'
        ),
    ),
    'POST /api/admin/sicherungen': (
        'Make a backup now',
        (
            'Writes a fresh backup and marks it as manual. Caches are stripped - they '
            'are nine tenths of the database and Nexview fetches them again by itself.'
        ),
    ),
    'POST /api/admin/sicherungen/einspielen': (
        'Restore into the running installation',
        (
            '⚠️ Afterwards nobody is signed in any more, including whoever triggered it '
            '- the accounts in the backup may not be the ones from a minute ago. A '
            'safety copy of the current state is written first.'
        ),
    ),
    'POST /api/admin/sicherungen/pruefen': (
        'Inspect a backup',
        (
            'Reports what is in it and whether this version can restore it. Nothing is '
            'replaced. A backup from a **newer** version is refused: the database can '
            'only be migrated forward.'
        ),
    ),
    'DELETE /api/admin/sicherungen/{name}': (
        'Delete a backup',
        (
            '⚠️ A delete button next to backups removes exactly what matters in an '
            'emergency, so callers are expected to ask first.'
        ),
    ),
    'POST /api/admin/sicherungen/{name}/archiv': (
        'Download a backup as an encrypted ZIP',
        (
            '⚠️ Deliberately `POST` and not `GET`: the password belongs in the body. In '
            'an address it would end up in the browser history and in every proxy log '
            'along the way. The archive is a plain AES ZIP - openable with 7-Zip, so '
            'nobody depends on Nexview to get at their own data.'
        ),
    ),
    # --- Anfragen entscheiden, Sperrliste, Titel und Personen --------------
    'GET /api/admin/requests': (
        'All requests',
        (
            'Every request from every account, optionally filtered by state, media type '
            'or account. For approvers and administrators.'
        ),
    ),
    'POST /api/admin/requests/approve-all/{user_id}': (
        'Approve everything from one account',
        (
            'Approves all open requests of one account at once. If handing one over to '
            'Radarr or Sonarr fails, the rest still go through - one unreachable '
            'instance should not block the whole batch.'
        ),
    ),
    'GET /api/admin/requests/pending/count': (
        'How many requests await a decision',
        (
            'A plain number for the menu badge. Restricted to accounts that may decide '
            '- a token inherits that from its owner.'
        ),
    ),
    'DELETE /api/admin/requests/{request_id}': (
        'Remove a request from the overview',
        (
            'Deletes the entry in Nexview only - in Radarr or Sonarr the title stays. '
            'Use cancel if the download should stop too.'
        ),
    ),
    'POST /api/admin/requests/{request_id}/approve': (
        'Approve a request',
        (
            'Hands the title over to Radarr or Sonarr and starts the download. The '
            'requester is notified.'
        ),
    ),
    'POST /api/admin/requests/{request_id}/cancel': (
        'Cancel a running request',
        (
            'Stops the request and deletes the title in Radarr or Sonarr, freeing the '
            "requester's quota again."
        ),
    ),
    'POST /api/admin/requests/{request_id}/defer': (
        'Put a request aside',
        (
            '"Yes in principle, just not now." Meant for the account that is over its '
            'quota: the approver wants neither to say no nor to wave it through. A '
            'deferred request comes back on its own at the next period change.'
        ),
    ),
    'POST /api/admin/requests/{request_id}/reject': (
        'Turn down a request',
        'The requester is notified and their quota is not charged.',
    ),
    'POST /api/admin/requests/{request_id}/reply': (
        "Reply to a requester's feedback",
        (
            '**Administrators only** - approvers decide about requests, but answering '
            'is a conversation, and that belongs with whoever runs the installation.'
        ),
    ),
    'GET /api/admin/blocklist': (
        'The blocklist',
        'Titles nobody may request. Administrators only.',
    ),
    'POST /api/admin/blocklist': (
        'Block a title',
        'Blocking something twice is not an error.',
    ),
    'DELETE /api/admin/blocklist/{media_type}/{tmdb_id}': (
        'Unblock a title',
        'The title can be requested again afterwards.',
    ),
    'GET /api/calendar': (
        'What arrives when',
        (
            'What appears in the given period - your own holdings and new releases. '
            'There is deliberately **no** region parameter: the region comes from the '
            'profile, so a calendar cannot show dates for a country the viewer does not '
            'live in.'
        ),
    ),
    'GET /api/browse/{media_type}': (
        'Everything under a keyword or studio',
        'All titles for one keyword or one studio, paged.',
    ),
    'GET /api/detail/tv/{tmdb_id}/season/{season_number}': (
        'Episodes of one season',
        'The episodes of a season, including which of them are already here.',
    ),
    'GET /api/detail/{media_type}/{tmdb_id}': (
        'Everything about one title',
        (
            'Cast, studios, keywords and recommendations, plus whether it is already in '
            'the library.'
        ),
    ),
    'GET /api/detail/{media_type}/{tmdb_id}/recommendations': (
        'A different set of recommendations',
        (
            'For the "show me others" button. The pool is fixed and always sorted the '
            'same way; `runde` only cuts a different slice out of it, so the button '
            'cannot loop.'
        ),
    ),
    'GET /api/people': (
        'People to browse and search',
        (
            'Filtered by department, paged. Without `q`, the most-asked-about people of '
            'that department.'
        ),
    ),
    'GET /api/person/{person_id}': (
        'One person',
        'Photo, biography and their best-known titles.',
    ),
    'GET /api/ratings/movie': (
        'Ratings for several movies at once',
        (
            'Deliberately its own call rather than part of the lists: the values come '
            'from Radarr, and twenty round trips would slow a page down for something '
            'nobody looks at first.'
        ),
    ),
    'GET /api/arr/{media_type}/options': (
        'Quality profiles and target folders',
        (
            'For the choice shown before adding something. Profiles blocked for this '
            'account are not returned at all, rather than being offered and refused '
            'later.'
        ),
    ),
    'GET /api/discover/{media_type}': (
        'Discover titles',
        (
            'Browsable lists of movies or shows with the usual filters - genre, year, '
            'rating, provider.'
        ),
    ),
    'GET /api/genres/{media_type}': (
        'Available genres',
        (
            'The genres TMDB knows for this media type, in the language of the calling '
            'account.'
        ),
    ),
    'GET /api/media/{media_type}/{tmdb_id}': (
        'Details for one title',
        (
            'Overview, cast, ratings, runtime, and whether it is already in the '
            'library.'
        ),
    ),
    'GET /api/search/{media_type}': (
        'Search movies or shows',
        (
            'Finds titles by name. Results are paged and come from TMDB in the language '
            'of the calling account.'
        ),
    ),
    'GET /api/studios': (
        'Known movie studios',
        'A pick list of well-known studios. Only meaningful for movies.',
    ),
    # --- Merken, Rueckmeldungen, Startseite, Benachrichtigungen, Anfragen --
    'GET /api/favorites': (
        'Your own marked titles',
        (
            'Filtered by the age limit of the calling account. The filtering is '
            "necessary because title and image come from Nexview's own table here, not "
            'from TMDB, and would otherwise bypass the check.'
        ),
    ),
    'POST /api/favorites': (
        'Mark a title',
        'Marking something twice is not an error.',
    ),
    'GET /api/favorites/people': (
        'Your own marked people',
        'Newest first.',
    ),
    'POST /api/favorites/people': (
        'Mark a person',
        'Doing it twice is not an error.',
    ),
    'DELETE /api/favorites/people/{person_id}': (
        'Unmark a person',
        'Removes the person from your favourites.',
    ),
    'DELETE /api/favorites/{media_type}/{tmdb_id}': (
        'Unmark a title',
        'What was not marked stays unmarked - no error.',
    ),
    'GET /api/feedback': (
        'All feedback',
        'Every quality rating in the installation, for whoever runs it.',
    ),
    'GET /api/feedback/mine': (
        'Your own feedback',
        'The quality ratings the calling account has given, with any replies.',
    ),
    'PUT /api/feedback/{media_type}/{tmdb_id}': (
        'Rate the quality of a title',
        (
            'How good the downloaded copy is. **Anyone may**, not just whoever '
            'requested it: this is about the file, and everybody watching it sees the '
            'same file.'
        ),
    ),
    'POST /api/feedback/{rating_id}/reply': (
        'Reply to a rating',
        (
            '**Administrators only.** Approvers see the ratings but may not answer '
            'them: a reply is a conversation, and that belongs with whoever runs the '
            'installation.'
        ),
    ),
    'GET /api/home/curated': (
        'Recommendations based on your favourites',
        (
            'Only what is not already here. **Movies and shows** - the first version '
            'looked at movies only, which left anyone who marks only shows with an empty '
            'row.'
        ),
    ),
    'GET /api/home/recent': (
        'Recently arrived',
        (
            'Titles that finished downloading, visible to everyone. ⚠️ Each entry also '
            'names **who requested it**. That is wanted inside a household and may not '
            'be wanted on a wall-mounted screen. Plot and backdrop come from TMDB; if '
            'that fails, the entries stay but look plainer.'
        ),
    ),
    'GET /api/home/trending': (
        'What is popular right now',
        (
            'Without the things that are already here. Deliberately filtered: a '
            'suggestion you can neither request nor want, because it is already '
            'downloaded, is not a suggestion.'
        ),
    ),
    'GET /api/health': (
        'Is Nexview running',
        (
            'Answers without a token - a monitor that has to sign in first is not a '
            'monitor. Returns nothing but a status, on purpose: no version, no database '
            'state, nothing that would tell an unauthenticated caller about the '
            'installation.'
        ),
    ),
    'GET /api/notifications': (
        'Your own notifications',
        'Newest first, with their read state.',
    ),
    'DELETE /api/notifications': (
        'Delete all your notifications',
        'Removes them rather than only marking them read.',
    ),
    'POST /api/notifications/read-all': (
        'Mark everything as read',
        'The notifications stay, they are only no longer unread.',
    ),
    'GET /api/notifications/unread/count': (
        'How many unread notifications',
        'A plain number for the bell badge.',
    ),
    'DELETE /api/notifications/{notification_id}': (
        'Delete one notification',
        'Removes a single entry.',
    ),
    'POST /api/notifications/{notification_id}/read': (
        'Mark one as read',
        'Marks a single notification as read.',
    ),
    'POST /api/requests': (
        'Request a title',
        (
            'Goes through exactly the same checks as a request made in the browser: '
            'quota, blocklist and approval all apply to the account the call is made '
            'for. A request needing approval comes back as pending, not as an error.'
        ),
    ),
    'GET /api/requests/mine': (
        'Your own requests',
        (
            'Every request from the calling account, newest first, with the rating '
            'attached to the title.'
        ),
    ),
    'GET /api/requests/quota': (
        'How much you may still request',
        (
            'What has been used in the current period and what is left. An account '
            'without a limit reports no ceiling rather than a very large number.'
        ),
    ),
    'DELETE /api/requests/{request_id}': (
        'Delete your own request entry',
        'Removes the entry. What has already been downloaded stays where it is.',
    ),
    'POST /api/requests/{request_id}/cancel': (
        'Cancel your own request',
        (
            'Useful when a title has not been found for days: the space in your quota '
            'is freed again. Only works while the request is still open.'
        ),
    ),
    'POST /api/requests/{request_id}/feedback': (
        'Rate your own request',
        (
            'Is the downloaded copy any good? The approvers are notified; a poor rating '
            'notifies them more clearly, because that is the case somebody has to act '
            'on.'
        ),
    ),
    # --- Stoebern, Streaming, Tickets, Warten, Merkliste -------------------
    'POST /api/stoebern/filmabend/ergebnis/{media_type}': (
        'Turn the answers into a shortlist',
        (
            '`runde` is the "roll again": the same answers in the same round always '
            'give the same stack, so going back and forth does not reshuffle what '
            'somebody was about to pick.'
        ),
    ),
    'GET /api/stoebern/filmabend/fragen/{media_type}': (
        'The whole question tree',
        (
            'Tailored to this person and delivered at once. The interface walks it '
            'itself - a round trip per question would be too slow for something meant '
            'to feel like a conversation.'
        ),
    ),
    'GET /api/stoebern/filter/{media_type}': (
        'Filter in plain questions',
        (
            'Six human questions instead of thirteen sliders. Deliberately **not** '
            'bolted onto discover: that page stays as it is.'
        ),
    ),
    'GET /api/stoebern/regal/{media_type}/{kennung}': (
        'The contents of one shelf',
        (
            '`bestand` filters by what is already here **on the server**. The discover '
            'page filters in the browser first, which on a full library leaves visible '
            'gaps in the grid.'
        ),
    ),
    'GET /api/stoebern/regale/{media_type}': (
        'Which shelves exist',
        (
            'Without a single TMDB call. The titles inside are fetched per shelf '
            'afterwards, so the overview appears immediately instead of waiting on a '
            'dozen lookups.'
        ),
    ),
    'GET /api/streaming': (
        'Streaming providers',
        (
            "The catalogue for the region, the account's own selection, and where that "
            'region came from.'
        ),
    ),
    'PUT /api/streaming': (
        'Replace your provider selection',
        (
            'Replaces the whole selection rather than changing one entry. One tick more '
            'or less is the same operation as "deselect everything".'
        ),
    ),
    'GET /api/tickets': (
        'Your tickets',
        "Your own; for an administrator, everyone's.",
    ),
    'POST /api/tickets': (
        'Open a ticket',
        'Creates a ticket with a first message. Goes to the administrators.',
    ),
    'POST /api/tickets/delete': (
        'Delete closed tickets for good',
        (
            '**Administrators only.** Deliberately `POST /delete` and not `DELETE`: a '
            'body does not belong on a DELETE, and this needs the list of what to '
            'remove.'
        ),
    ),
    'POST /api/tickets/kontoaufloesung': (
        'Apply to have your account deleted',
        (
            'For users and approvers. Arrives as an ordinary ticket with the '
            'administrators, who see the consequences before they decide.'
        ),
    ),
    'PATCH /api/tickets/messages/{message_id}': (
        'Edit a message',
        "Corrects the text of one's own message in a ticket.",
    ),
    'GET /api/tickets/open-count': (
        'How many tickets need attention',
        (
            "For the menu badge. For an administrator, everybody's open tickets; for "
            'everyone else, their own ones that have been answered.'
        ),
    ),
    'GET /api/tickets/{ticket_id}': (
        'One ticket',
        'The ticket with all its messages.',
    ),
    'PATCH /api/tickets/{ticket_id}': (
        "Set a ticket's state",
        'Administrators only.',
    ),
    'POST /api/tickets/{ticket_id}/kinderkonten-freischalten': (
        'Grant child profiles from a ticket',
        (
            'Settles the application in one click - grants the permission, replies, '
            'closes the ticket.'
        ),
    ),
    'POST /api/tickets/{ticket_id}/messages': (
        'Reply in a ticket',
        'Adds a message to the conversation.',
    ),
    'GET /api/watch': (
        'What you are waiting for',
        'Titles marked as "tell me when this arrives".',
    ),
    'DELETE /api/watch/{media_type}/{tmdb_id}': (
        'Stop waiting for a title',
        (
            'No 404 if nothing was marked: the goal is "stop telling me about this", '
            'and that is satisfied either way.'
        ),
    ),
    'PUT /api/watch/{media_type}/{tmdb_id}': (
        'Wait for a title',
        (
            'Clicking twice is a double click, not an error. Deliberately `PUT` and not '
            '`POST`: the call describes a state, not an event.'
        ),
    ),
    'POST /api/watchlist/connect/poll': (
        'Check whether the watchlist link was confirmed',
        (
            'Stores the personal access once confirmed. It has to be the **same** '
            'account that is already linked - otherwise somebody could hang a '
            "stranger's watchlist on their own profile."
        ),
    ),
    'POST /api/watchlist/connect/start': (
        'Start linking a watchlist',
        (
            'Opens the provider flow for granting Nexview access to a personal '
            'watchlist.'
        ),
    ),
    'GET /api/watchlist/plex': (
        'Your Plex watchlist',
        'What is on the linked watchlist, and which of it is already here.',
    ),
    'GET /api/watchlist/status': (
        'Is a watchlist available',
        'Whether a personal access exists and whether an account is linked.',
    ),
    'GET /api/settings/instanzen/downloadkollision': (
        'Do two instances share a download category',
        (
            'Radarr and Sonarr only see what sits in their own category of '
            'the download client. If two instances share one, each grabs the '
            'downloads the other one queued: requests hang, files land in '
            'the wrong place, and no error appears anywhere - so the '
            'operator goes looking at the network. Radarr cannot warn about '
            'this: it does not know the second instance exists. Nexview does.'
        ),
    ),
    'POST /api/settings/instanzen/downloadkollision/ignorieren': (
        'Stop reporting this collision',
        (
            'Some setups share a category on purpose. Dismissing is '
            'permanent, but tied to the instances involved - adding a third '
            'one to the same category reports again, because that is a new '
            'mistake and not a dismissed old one.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/ausfuhr': (
        'Take the profile store with you',
        (
            'Every profile kept in Nexview as one file: names, recipes and '
            'the instances each was last written to. It carries no '
            'credentials and no keys, so it can be handed to somebody else. '
            'Needed because Nexview keeps the record of ownership in its own '
            'database only - a fresh installation pointed at the same Radarr '
            'stands before its own profiles as before strangers.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile/einfuhr/vorschau': (
        'What the import would do',
        (
            'Reads the file and looks at every instance: which profiles '
            'would be taken over, which differ from their recipe, which are '
            'not there at all. Changes nothing.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile/einfuhr': (
        'Bring the profile store back',
        (
            'Adds the profiles to Nexview and takes over the copies found on '
            'the instances by name, recording their id. Nothing is written to '
            'Radarr or Sonarr - a copy that differs from its recipe is '
            'adopted as it stands and shown as adjusted. A name already in '
            'the store is skipped rather than overwritten.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile': (
        'Quality profiles kept in Nexview',
        (
            'The profiles you created here, each with the instances it has '
            'been written to. A profile lives in Nexview; the copies on the '
            'instances are made from it.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile': (
        'Keep a new quality profile',
        (
            'Stores the answers from the guide. Nothing is written to Radarr '
            'or Sonarr yet - that is a separate step.'
        ),
    ),
    'DELETE /api/settings/qualitaetsprofile/{profil_id}': (
        'Forget a quality profile',
        (
            'Removes it from Nexview. Copies already written to an instance '
            'stay there: deleting a profile that titles are assigned to would '
            'damage those titles.'
        ),
    ),
    'PUT /api/settings/qualitaetsprofile/{profil_id}/instanzen': (
        'Decide where the profile lives',
        (
            'Writes the profile and its detection patterns to every instance '
            'listed, and stops managing it on the others. The list is the '
            'truth, not a set of changes.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/quelle': (
        'Which TRaSH snapshot is in use',
        (
            'Date, origin and licence of the guide data, whether it is still '
            'the bundled one, and whether a newer state has been seen.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile/quelle/aktualisieren': (
        'Fetch the current TRaSH state',
        (
            'Downloads the guide data from GitHub and adopts it, but only if '
            'every profile you keep can still be built from it. Nothing is '
            'written to Radarr or Sonarr by this: afterwards the comparison '
            'shows which copies have fallen behind, and you decide.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/benennung': (
        'How each instance names files and folders',
        (
            'What is set right now, next to what the TRaSH Guides recommend '
            'for the media server you have connected.'
        ),
    ),
    'PUT /api/settings/qualitaetsprofile/benennung': (
        'Adopt the recommended naming scheme',
        (
            'Sets the file and/or folder scheme on one instance. It applies to '
            'what the instance writes from now on; files already on disk are '
            'not touched. Renaming an existing library is a separate step in '
            'Radarr or Sonarr, with consequences for seeding and the media '
            'server.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/medienserver': (
        'Which instance knows which media server',
        (
            'Radarr and Sonarr only tell a media server about imports, '
            'upgrades and renames when a connection is set up. This lists '
            'where one is missing.'
        ),
    ),
    'PUT /api/settings/qualitaetsprofile/medienserver/schluessel': (
        'Store the API key of a media server',
        (
            'Jellyfin and Emby need a key from their own dashboard. It is not '
            'the access Nexview itself uses: that one comes from a username '
            'and password and ends with the session.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile/medienserver/verbinden': (
        'Create the missing connections',
        (
            'Each one is tested from the instance first and only written when '
            'the test succeeds. A connection that never worked is worse than '
            'none, because nobody questions it later.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/benennung/{kennung}/fortschritt': (
        'How far the library rename has got',
        (
            'Radarr and Sonarr report only whether a command is running, never '
            'how far along it is, so Nexview splits the work into batches and '
            'counts them itself. Two phases: checking every title (read only), '
            'then renaming the ones that change.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile/benennung/{kennung}/altnamen': (
        'Drop the old prefix from format names',
        (
            'Earlier versions prefixed every custom format Nexview created. '
            'That prefix reaches the file name whenever a format is marked to '
            'appear there, so a library rename would write it into thousands '
            'of files. This renames those formats back, keeping their ids so '
            'profiles keep pointing at them. Formats whose plain name is '
            'already taken by someone else are left untouched and reported.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/bestand': (
        'Everything on the instances',
        (
            'Every quality profile and custom format that exists in Radarr and '
            'Sonarr, including the ones Nexview did not create, together with '
            'what depends on each: media, import lists and collections. The '
            'instances themselves refuse a deletion with "in use" without '
            'naming who is using it - this answers that question.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile/bestand/{kennung}/umhaengen': (
        'Move media to another profile',
        (
            'Reassigns every movie or series that currently sits on one quality '
            'profile to another one. Files are not touched - only the assignment '
            'changes. This is what makes cleaning up possible at all: a profile '
            'cannot be deleted while media sit on it, and in a grown setup almost '
            'everything sits on profiles that predate Nexview. Note that the new '
            'profile scores differently, so titles whose existing file falls below '
            'it will be queued for an upgrade.'
        ),
    ),
    'POST /api/settings/qualitaetsprofile/bestand/{kennung}/aufraeumen': (
        'Remove selected profiles and formats',
        (
            'Deletes what the operator picked, checking each entry against the '
            'instance again immediately beforehand - what is in use by then is '
            'refused with the reason instead of forced. Profiles go first so '
            'that formats which only hung on them can follow in the same run. '
            'This is the one place where Nexview removes things it did not '
            'create; the mandate comes from the selection.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/abgleich': (
        'Do the copies still match',
        (
            'Asks every instance whether the profile written there still looks '
            'the way Nexview left it, and whether the guide data has moved on '
            'since. Separate from the list so a silent instance cannot hold up '
            'the page.'
        ),
    ),
    'GET /api/settings/qualitaetsprofile/{profil_id}/fortschritt': (
        'How far the writing has got',
        (
            'Radarr and Sonarr accept detection patterns one at a time, so '
            'writing a profile keeps the connection open for a minute or more. '
            'This says which instance is being written and how many patterns '
            'are done.'
        ),
    ),
}


def _schluessel(methode: str, pfad: str) -> str:
    return f"{methode} {pfad}"


def anwenden(app: FastAPI) -> None:
    """Die englischen Texte ueber die Routen legen.

    ⚠️ **Ausdrueckliche Texte werden nicht ueberschrieben.** Traegt eine Route
    schon ein eigenes ``description=`` - so wie die dreizehn unter ``/api/v1``,
    wo der Text neben der Zusage stehen muss - bleibt es dabei. Sonst haette
    diese Datei die Hoheit ueber etwas, das anderswo bewusst entschieden wurde.

    Aufzurufen **nachdem** alle Router eingehaengt sind; vorher gibt es die
    Routen noch nicht.
    """
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue

        doku = (inspect.getdoc(route.endpoint) or "").strip()
        vorhanden = (route.description or "").strip()
        if vorhanden and vorhanden != doku:
            continue

        for methode in route.methods - {"HEAD", "OPTIONS"}:
            eintrag = TEXTE.get(_schluessel(methode, route.path))
            if eintrag is None:
                continue
            route.summary, route.description = eintrag
            break


def fehlende(app: FastAPI) -> list[str]:
    """Welche Operationen noch keinen englischen Text haben.

    Fuer den Test - und fuer den Menschen, der die Datei fuellt: Die Liste sagt
    ihm, was noch fehlt, statt ihn suchen zu lassen.
    """
    offen: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        doku = (inspect.getdoc(route.endpoint) or "").strip()
        for methode in sorted(route.methods - {"HEAD", "OPTIONS"}):
            beschreibung = (route.description or "").strip()
            # Kein Text, oder der deutsche Docstring durchgereicht.
            if not beschreibung or beschreibung == doku:
                offen.append(_schluessel(methode, route.path))
    return sorted(offen)


def verwaiste(app: FastAPI) -> list[str]:
    """Texte fuer Adressen, die es nicht mehr gibt.

    ⚠️ Die andere Richtung, und sie ist genauso wichtig: Wird eine Adresse
    umbenannt, faellt ihr Text hier lautlos aus der Anwendung heraus und
    bleibt als Karteileiche stehen. Beim naechsten Lesen glaubt jemand, sie
    sei beschrieben.
    """
    da = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        for methode in route.methods - {"HEAD", "OPTIONS"}:
            da.add(_schluessel(methode, route.path))
    return sorted(set(TEXTE) - da)
