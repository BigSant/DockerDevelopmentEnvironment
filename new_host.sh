#!/bin/bash

user_id="$(id -u)"
if [[ "$user_id" == "0" ]]; then
   echo "Please run as regular user (without sudo)"
   exit 1
fi

# Local private variables
user="$USER"
current_dir="$(readlink -f "$0")"
current_dir="$(dirname "$current_dir")"
docker_dir="$current_dir/docker"

# Local configurable variables
projects_dir="/home/$user/Projects"
index_file="$current_dir/index_counter"

# Development environment variables
extension="local"
subdomains=('pma' 'mailpit')
domain="$1"

# Ports
base_port=3000
port=0
port_ssl=0

update_config()
{
local search
search=$1

local replace
replace=$2

local file
file=$3

echo -n "File: $file, config: $replace updating: "
if [[ ! -e "$file" ]]; then
  echo "file not found or not writable"
else
  local search_exists
  search_exists="$(sudo grep "$search" "$file")"
  if [[ -n "$search_exists" ]]; then
    sudo sed -i 's#'"$search"'#'"$replace"'#g' "$file"

    search_exists="$(sudo grep "$search" "$file")"
    if [[ -n "$search_exists" ]]; then
      echo "failed match query: '$search'"
    else
      echo "updated"
    fi
  else
    echo "already updated"
  fi
fi
}

if [[ -z "$domain" ]]; then
  echo "Domain is required to create new host"
  exit 1
fi

if [[ ! $(command -v "mkcert" 2>/dev/null) ]] || [[ ! $(command -v "certutil" 2>/dev/null) ]]; then
  echo "mkcert or libnss3-tools not installed, install package before creating hosts"
  exit 1
fi

if [[ ! -f $index_file ]]; then
  echo 0 > $index_file
fi

domain_dir="$projects_dir/$domain"

if [[ ! -f /etc/nginx/conf.d/connection_upgrade.conf ]]; then
  echo 'map $http_upgrade $connection_upgrade {
  default upgrade;
  ""      "";
}' | sudo tee /etc/nginx/conf.d/connection_upgrade.conf > /dev/null

 sudo service nginx restart
fi

# Install CA
mkcert -install

index=$(head -n 1 $index_file)
index=$((index + 1))
port=$((base_port + index))
index=$((index + 1))
port_ssl=$((base_port + index))

# Create directory for project
if [[ ! -e "$domain_dir" ]]; then
  mkdir -p "$domain_dir/app"
  mkdir -p "$domain_dir/data" # Store cache for MySQL, Mailhog, Xdebug profiler, etc.
  mkdir -p "$domain_dir/backup/$(date "+%Y-%m-%d")"

  # Docker structure
  app_dir="$domain_dir/app"
  mkdir -p "$app_dir/public"
  mkdir -p "$app_dir/docs"
  mkdir -p "$app_dir/config" # Store configuration for psalm, phpstan, etc.
  app_docker_dir="$app_dir/docker"
  mkdir -p "$app_docker_dir"
  
  cp "$docker_dir/Makefile.local" "$app_docker_dir/Makefile"
  update_config "{docker_dir}" "$docker_dir" "$app_docker_dir/Makefile"
  
  echo "PROJECT_NAME=$domain
" | tee "$app_docker_dir/.env" > /dev/null

  echo "DOMAIN=$domain.$extension
LOCALHOST_PORT=$port
LOCALHOST_PORT_SSL=$port_ssl

DATABASE_USER=$domain
DATABASE_PASSWORD=$domain
DATABASE_NAME=$domain
" | tee "$app_docker_dir/.env.local" > /dev/null
fi

# Generate certificate for SSL or regenerate if expiring within 30 days
if [[ ! -f $domain_dir/data/ssl/domain.crt ]] || ! openssl x509 -checkend 2592000 -noout -in $domain_dir/data/ssl/domain.crt 2>/dev/null; then
  mkdir -p $domain_dir/data/ssl
  mkcert -cert-file $domain_dir/data/ssl/domain.crt -key-file $domain_dir/data/ssl/domain.key "$domain.$extension" "*.$domain.$extension"
  chmod 644 "$domain_dir/data/ssl/domain.crt"
  chmod 600 "$domain_dir/data/ssl/domain.key"
fi

# Create nginx reverse proxy for new domain
if [[ ! -e "/etc/nginx/sites-available/$domain.$extension.conf" ]]; then
  echo "$index" | tee "$index_file" > /dev/null
  
  echo "# HTTP
server {
  listen 80;
  listen [::]:80;
  server_name $domain.$extension pma.$domain.$extension mailpit.$domain.$extension;
  error_log $projects_dir/logs/error.log;
  access_log $projects_dir/logs/access.log;
  location / {
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Forwarded-Proto http;
    proxy_pass http://127.0.0.1:$port;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_buffering off;
    proxy_connect_timeout 3600;
    proxy_send_timeout 3600;
    proxy_read_timeout 3600;
    send_timeout 3600;
  }
}

# HTTPS
server {
  listen 443 ssl http2;
  listen [::]:443 ssl http2;
  server_name $domain.$extension pma.$domain.$extension mailpit.$domain.$extension;
  ssl_certificate $domain_dir/data/ssl/domain.crt;
  ssl_certificate_key $domain_dir/data/ssl/domain.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_ciphers HIGH:!aNULL:!MD5;
  ssl_prefer_server_ciphers on;
  ssl_session_cache shared:SSL:10m;
  ssl_session_timeout 10m;
  error_log $projects_dir/logs/error.log;
  access_log $projects_dir/logs/access.log;
  location / {
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header Host \$http_host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_pass http://127.0.0.1:$port;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_buffering off;
    proxy_connect_timeout 3600;
    proxy_send_timeout 3600;
    proxy_read_timeout 3600;
    send_timeout 3600;
  }
}
" | sudo tee "/etc/nginx/sites-available/$domain.$extension.conf" > /dev/null

  sudo ln -s "/etc/nginx/sites-available/$domain.$extension.conf" "/etc/nginx/sites-enabled/$domain.$extension.conf"
  
  sudo service nginx restart
else
  echo "Nginx conf already created"
fi

# Add for /etc/hosts file links
if [[ -z "$(sudo grep -F "127.0.0.1 $domain.$extension" /etc/hosts)" ]]; then
  echo "# $domain" | sudo tee -a /etc/hosts > /dev/null
  echo "127.0.0.1 $domain.$extension" | sudo tee -a /etc/hosts > /dev/null
else
  echo "Domain already added to host file"
fi

for subdomain in ${subdomains[@]}; do
  if [[ -z "$(sudo grep -F "127.0.0.1 $subdomain.$domain.$extension" /etc/hosts)" ]]; then
    echo "127.0.0.1 $subdomain.$domain.$extension" | sudo tee -a /etc/hosts > /dev/null
  else
    echo "Subdomain already added to host file"
  fi
done

exit 0
