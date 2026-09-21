# Users, passwords and customer cameras

Sign in at https://shopaware.innawareucp.com. Your existing administrator username, password, users and assignments remain unchanged after the beta.4 upgrade.

## Change your own password

1. Open **My account** in the sidebar.
2. Enter your current password, a new password of at least 12 characters, and its confirmation.
3. Click **Change password**, then sign in again with the new password.

This signs out your other sessions too. Passwords cannot be displayed or recovered; an administrator can reset another user's password in **Users**.

## Organize cameras by customer

1. Open **Customers** and create a group named for the customer.
2. Open **Cameras**. Select the customer group when adding a camera, or change an existing camera's customer group.
3. Use the customer filters in **Overview**, **Cameras**, **Incidents**, and **Analytics** to narrow the view. Customer cards also link to their cameras.

Each camera belongs to one customer group, or is **Ungrouped**. Groups can be renamed. Move or remove their cameras before deleting a group.

Moving a camera between groups clears its individual user grants and resets its stream/tracking buffers. Earlier incidents stay available to administrators only, including if the camera later returns to its previous group. Reassign individual access deliberately after a move. This also applies when assigning an initially ungrouped camera to a customer.

## Add users and assign access

1. Open **Users** and click **Add user**.
2. Enter a unique username and choose a role:
   - **User** can view and review assigned cameras and their incidents, and change their own password.
   - **Administrator** can see every camera/customer and manage users, camera configuration, training, and settings.
3. For a regular user, select **Customer group access**, **Individual camera access**, or both. Group access automatically includes current and future cameras in that group. Individual selections add access; they do not exclude cameras already granted by a group. No selections means no camera access.
4. Enter an initial password of at least 12 characters and click **Save user**. Give the credentials directly to that person. They can change the password in **My account**.

Select a user to change assignments, disable the account, reset its password, or delete it. Saving edits, resetting a password, disabling, or deleting an account revokes its active sessions. Camera group membership changes take effect on subsequent requests and live updates. An administrator cannot disable, delete, or demote their own account; at least one enabled administrator must remain.

## What the separation covers

Camera lists, live WebSocket previews, camera frames, incident history/detail, analytics observations, snapshots, clips, review actions, customer lists and incident counts are checked on the server. Regular users cannot access camera credentials, training datasets, administrative settings or user management by entering an API URL directly.

Customer groups share one ShopAware installation and database. Administrators are trusted across every customer. SMTP delivery, inference settings and training remain administrator-managed and global; groups do not configure separate customer email destinations. Existing retention settings still apply across the installation.

## Upgrade and rollback

Beta.4 migrates the database to schema 5, preserving accounts, password hashes, camera credentials and evidence and defaulting existing cameras to Shoplifting mode. Back up the database, its matching encryption key, configuration and evidence before upgrading, following the [beta.4 Server2 upgrade guide](SERVER2_BETA4_UPGRADE.md). Rolling back requires restoring the complete pre-upgrade database and matching key; older releases cannot open schema 5.
