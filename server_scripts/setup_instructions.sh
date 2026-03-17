#!/bin/bash
cat << 'INSTRUCTIONS'
==========================================================
  Magento CSV Export Setup — Step by Step
==========================================================

1. CREATE THE PASSWORD FILE
   ---------------------------------------------------------
   ssh eplattforma@your-server

   cat > /home/eplattforma/.db_export_env << 'EOF'
   DB_PASSWORD="YOUR_READONLY_DB_PASSWORD_HERE"
   EOF

   chmod 600 /home/eplattforma/.db_export_env

2. UPLOAD THE SCRIPTS
   ---------------------------------------------------------
   Upload these two files to the server:
     - export_customer_login_logs.sh
     - export_customer_price_master.sh

   Place them in: /home/eplattforma/scripts/

   mkdir -p /home/eplattforma/scripts
   chmod +x /home/eplattforma/scripts/export_customer_login_logs.sh
   chmod +x /home/eplattforma/scripts/export_customer_price_master.sh

3. CREATE LOG DIRECTORY
   ---------------------------------------------------------
   mkdir -p /home/eplattforma/logs

4. TEST MANUALLY
   ---------------------------------------------------------
   bash /home/eplattforma/scripts/export_customer_login_logs.sh
   echo "Login logs exit code: $?"
   cat /home/eplattforma/logs/export_login_logs.log

   bash /home/eplattforma/scripts/export_customer_price_master.sh
   echo "Price master exit code: $?"
   cat /home/eplattforma/logs/export_price_master.log

   # Verify output files
   head -3 /home/eplattforma/public_html/eplattforma.com.cy/ReplitConnect/customer_login_logs.csv
   wc -l /home/eplattforma/public_html/eplattforma.com.cy/ReplitConnect/customer_login_logs.csv

   head -3 /home/eplattforma/public_html/eplattforma.com.cy/ReplitConnect/customer_price_master.csv
   wc -l /home/eplattforma/public_html/eplattforma.com.cy/ReplitConnect/customer_price_master.csv

5. ADD CRON JOBS
   ---------------------------------------------------------
   crontab -e

   # Add these lines:
   # Login logs — daily at 2:00 AM server time
   0 2 * * * /bin/bash /home/eplattforma/scripts/export_customer_login_logs.sh >> /home/eplattforma/logs/cron_login.log 2>&1

   # Price master — daily at 2:30 AM server time
   30 2 * * * /bin/bash /home/eplattforma/scripts/export_customer_price_master.sh >> /home/eplattforma/logs/cron_price.log 2>&1

6. VERIFY CRON
   ---------------------------------------------------------
   crontab -l   # should show both jobs

==========================================================
INSTRUCTIONS
