// pm2 ecosystem for FinRobot Moonshot.
// Usage:
//   pm2 start ecosystem.config.js
//   pm2 logs
//   pm2 save                  # persist process list
//   pm2 startup                # generate boot script (one-time)
//
// Both processes load secrets from ~/FinRobot/.env via python-dotenv,
// so no secrets are hard-coded here.

module.exports = {
  apps: [
    {
      name: "moonshot-daemon",
      cwd: "/home/openclaw/FinRobot",
      script: "scripts/run_daemon.py",
      interpreter: "/home/openclaw/FinRobot/.venv/bin/python",
      args: "--interval 60 --balance 100",
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 50,
      out_file: "/home/openclaw/FinRobot/logs/pm2_daemon.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_daemon.err.log",
      time: true,
    },
    {
      name: "moonshot-improver",
      cwd: "/home/openclaw/FinRobot",
      script: "scripts/run_improver.py",
      interpreter: "/home/openclaw/FinRobot/.venv/bin/python",
      autorestart: true,
      restart_delay: 30000,
      max_restarts: 50,
      out_file: "/home/openclaw/FinRobot/logs/pm2_improver.out.log",
      error_file: "/home/openclaw/FinRobot/logs/pm2_improver.err.log",
      time: true,
    },
  ],
};
