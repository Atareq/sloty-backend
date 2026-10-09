sloty_die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

sloty_require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null 2>&1 ||
      sloty_die "Required command not found: ${command_name}"
  done
}

sloty_require_env() {
  local variable_name
  for variable_name in "$@"; do
    [[ -n "${!variable_name:-}" ]] ||
      sloty_die "Required environment variable is missing: ${variable_name}"
  done
}

sloty_require_database_connection() {
  sloty_require_env DB_ENGINE DB_USER DB_PASSWORD DB_HOST DB_PORT
  case "${DB_ENGINE,,}" in
    postgres | postgresql) ;;
    *) sloty_die "DB_ENGINE must be PostgreSQL for database backup or restore" ;;
  esac

  [[ "$DB_PORT" =~ ^[1-9][0-9]{0,4}$ ]] ||
    sloty_die "DB_PORT must be a valid TCP port"
  (( DB_PORT <= 65535 )) || sloty_die "DB_PORT must be a valid TCP port"

  export PGHOST="$DB_HOST"
  export PGPORT="$DB_PORT"
  export PGUSER="$DB_USER"
  export PGPASSWORD="$DB_PASSWORD"
  if [[ -n "${DB_SSLMODE:-}" ]]; then
    export PGSSLMODE="$DB_SSLMODE"
  else
    unset PGSSLMODE
  fi
}

sloty_require_backup_database() {
  sloty_require_env DB_NAME
  [[ "$DB_NAME" == "sloty_prod" ]] ||
    sloty_die "Refusing to back up a database other than sloty_prod"
}

sloty_configure_rclone() {
  sloty_require_env R2_ENDPOINT R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET_BACKUP
  [[ "$R2_BUCKET_BACKUP" == "sloty-prod-backups" ]] ||
    sloty_die "R2_BUCKET_BACKUP must be sloty-prod-backups"

  [[ "$R2_ENDPOINT" == https://* ]] ||
    sloty_die "R2_ENDPOINT must use HTTPS"

  export RCLONE_CONFIG=/dev/null
  export RCLONE_CONFIG_SLOTY_TYPE=s3
  export RCLONE_CONFIG_SLOTY_PROVIDER=Cloudflare
  export RCLONE_CONFIG_SLOTY_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_SLOTY_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_SLOTY_REGION=auto
  export RCLONE_CONFIG_SLOTY_ENDPOINT="$R2_ENDPOINT"
  export RCLONE_CONFIG_SLOTY_NO_CHECK_BUCKET=true
  export RCLONE_CONFIG_SLOTY_NO_HEAD=true
}

sloty_rclone() {
  rclone --config=/dev/null --retries=1 --low-level-retries=1 "$@"
}
