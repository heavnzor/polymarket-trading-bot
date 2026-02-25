const path = require("path");
const HOME = process.env.HOME || "/home/user";

module.exports = {
  apps: [
    {
      name: "polybot",
      cwd: path.join(HOME, "polymarket"),
      script: ".venv/bin/python",
      args: "services/worker/run_worker.py",
      interpreter: "none",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      // Log management
      error_file: path.join(HOME, ".pm2/logs/polybot-error.log"),
      out_file: path.join(HOME, ".pm2/logs/polybot-out.log"),
      log_date_format: "YYYY-MM-DDTHH:mm:ss",
      merge_logs: true,
      max_memory_restart: "1500M",
      // Restart policy
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      // Graceful shutdown
      kill_timeout: 15000,
      listen_timeout: 10000,
    },
  ],
};
