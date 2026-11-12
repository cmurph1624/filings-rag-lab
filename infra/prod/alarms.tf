# Self-healing for the one server. EC2 publishes both status checks every minute for free.
#
# System check failed (AWS hardware, host or network): recover, i.e. restart the same instance
# on healthy hardware, keeping its ID, Elastic IP and EBS disk (the database). Instance check
# failed (the OS is wedged: out of memory, kernel panic): reboot. Docker restarts the stack on
# boot from the containers' stored config, so neither needs the deploy's env files.
#
# Two alarms, about $0.20/month. No notifications: the ops health check (scripts/prod_health.sh)
# is where a failure shows.

resource "aws_cloudwatch_metric_alarm" "recover" {
  alarm_name          = "${local.name}-recover"
  alarm_description   = "System status check failed: recover ${local.name} onto healthy hardware."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:recover"]
}

resource "aws_cloudwatch_metric_alarm" "reboot" {
  alarm_name          = "${local.name}-reboot"
  alarm_description   = "Instance status check failed: reboot ${local.name}."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:reboot"]
}
