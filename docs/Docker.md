# Docker Setup Guide

MemoPad can be run in Docker containers to provide a consistent, isolated environment for your knowledge management
system. This is particularly useful for integrating with existing Dockerized MCP servers or for deployment scenarios.

## Quick Start

### Option 1: Using Pre-built Images (Recommended)

MemoPad provides pre-built Docker images on GitHub Container Registry that are automatically updated with each release.

1. **Use the official image directly:**
   ```bash
   docker run -d \
     --name memopad-server \
     -p 8000:8000 \
     -v /path/to/your/obsidian-vault:/app/data:rw \
     -v memopad-config:/app/.memopad:rw \
     ghcr.io/basicmachines-co/memopad:latest
   ```

2. **Or use Docker Compose with the pre-built image:**
   ```yaml
   version: '3.8'
   services:
     memopad:
       image: ghcr.io/basicmachines-co/memopad:latest
       container_name: memopad-server
       ports:
         - "8000:8000"
       volumes:
         - /path/to/your/obsidian-vault:/app/data:rw
         - memopad-config:/app/.memopad:rw
       environment:
         - MEMOPAD_DEFAULT_PROJECT=main
       restart: unless-stopped
   ```

### Option 2: Using Docker Compose (Building Locally)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/basicmachines-co/memopad.git
   cd memopad
   ```

2. **Update the docker-compose.yml:**
   Edit the volume mount to point to your Obsidian vault:
   ```yaml
   volumes:
     # Change './obsidian-vault' to your actual directory path
     - /path/to/your/obsidian-vault:/app/data:rw
   ```

3. **Start the container:**
   ```bash
   docker-compose up -d
   ```

### Option 3: Using Docker CLI

```bash
# Build the image
docker build -t memopad .

# Run with volume mounting
docker run -d \
  --name memopad-server \
  -v /path/to/your/obsidian-vault:/app/data:rw \
  -v memopad-config:/app/.memopad:rw \
  -e MEMOPAD_DEFAULT_PROJECT=main \
  memopad
```

## Configuration

### Volume Mounts

MemoPad requires several volume mounts for proper operation:

1. **Knowledge Directory** (Required):
   ```yaml
   - /path/to/your/obsidian-vault:/app/data:rw
   ```
   Mount your Obsidian vault or knowledge base directory.

2. **Configuration and Database** (Recommended):
   ```yaml
   - memopad-config:/app/.memopad:rw
   ```
   Persistent storage for configuration and SQLite database.

You can edit the memopad config.json file located in the /app/.memopad/config.json after MemoPad starts.

3. **Multiple Projects** (Optional):
   ```yaml
   - /path/to/project1:/app/data/project1:rw
   - /path/to/project2:/app/data/project2:rw
   ```

You can edit the memopad config.json file located in the /app/.memopad/config.json

## CLI Commands via Docker

You can run MemoPad CLI commands inside the container using `docker exec`:

### Basic Commands

```bash
# Check status
docker exec memopad-server memopad status

# Sync files
docker exec memopad-server memopad sync

# Show help
docker exec memopad-server memopad --help
```

### Managing Projects with Volume Mounts

When using Docker volumes, you'll need to configure projects to point to your mounted directories:

1. **Check current configuration:**
   ```bash
   docker exec memopad-server cat /app/.memopad/config.json
   ```

2. **Add a project for your mounted volume:**
   ```bash
   # If you mounted /path/to/your/vault to /app/data
   docker exec memopad-server memopad project create my-vault /app/data
   
   # Set it as default
   docker exec memopad-server memopad project set-default my-vault
   ```

3. **Sync the new project:**
   ```bash
   docker exec memopad-server memopad sync
   ```

### Example: Setting up an Obsidian Vault

If you mounted your Obsidian vault like this in docker-compose.yml:
```yaml
volumes:
  - /Users/yourname/Documents/ObsidianVault:/app/data:rw
```

Then configure it:
```bash
# Create project pointing to mounted vault
docker exec memopad-server memopad project create obsidian /app/data

# Set as default
docker exec memopad-server memopad project set-default obsidian

# Sync to index all files
docker exec memopad-server memopad sync
```

### Environment Variables

Configure MemoPad using environment variables:

```yaml
environment:

  # Default project
  - MEMOPAD_DEFAULT_PROJECT=main

  # Enable real-time sync
  - MEMOPAD_SYNC_CHANGES=true

  # Logging level
  - MEMOPAD_LOG_LEVEL=INFO

  # Sync delay in milliseconds
  - MEMOPAD_SYNC_DELAY=1000
```

## File Permissions

### Linux/macOS

The Docker container now runs as a non-root user to avoid file ownership issues. By default, the container uses UID/GID 1000, but you can customize this to match your user:

```bash
# Build with custom UID/GID to match your user
docker build --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t memopad .

# Or use docker-compose with build args
```

**Example docker-compose.yml with custom user:**
```yaml
version: '3.8'
services:
  memopad:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        UID: 1000  # Replace with your UID
        GID: 1000  # Replace with your GID
    container_name: memopad-server
    ports:
      - "8000:8000"
    volumes:
      - /path/to/your/obsidian-vault:/app/data:rw
      - memopad-config:/app/.memopad:rw
    environment:
      - MEMOPAD_DEFAULT_PROJECT=main
    restart: unless-stopped
```

**Using pre-built images:**
If using the pre-built image from GitHub Container Registry, files will be created with UID/GID 1000. You can either:

1. Change your local directory ownership to match:
   ```bash
   sudo chown -R 1000:1000 /path/to/your/obsidian-vault
   ```

2. Or build your own image with custom UID/GID as shown above.

### Windows

When using Docker Desktop on Windows, ensure the directories are shared:

1. Open Docker Desktop
2. Go to Settings → Resources → File Sharing
3. Add your knowledge directory path
4. Apply & Restart

## Troubleshooting

### Common Issues

1. **File Watching Not Working:**
    - Ensure volume mounts are read-write (`:rw`)
    - Check directory permissions
    - On Linux, may need to increase inotify limits:
      ```bash
      echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
      sudo sysctl -p
      ```

2. **Configuration Not Persisting:**
    - Use named volumes for `/app/.memopad`
    - Check volume mount permissions

3. **Network Connectivity:**
    - For HTTP transport, ensure port 8000 is exposed
    - Check firewall settings

### Debug Mode

Run with debug logging:

```yaml
environment:
  - MEMOPAD_LOG_LEVEL=DEBUG
```

View logs:

```bash
docker-compose logs -f memopad
```


## Security Considerations

1. **Docker Security:**
   The container runs as a non-root user (UID/GID 1000 by default) for improved security. You can customize the user ID using build arguments to match your local user.

2. **Volume Permissions:**
   Ensure mounted directories have appropriate permissions and don't expose sensitive data. With the non-root container, files will be created with the specified user ownership.

3. **Network Security:**
   If using HTTP transport, consider using reverse proxy with SSL/TLS and authentication if the endpoint is available on
   a network.

4. **IMPORTANT:** The HTTP endpoints have no authorization. They should not be exposed on a public network.  

## Integration Examples

### Claude Desktop with Docker

The recommended way to connect Claude Desktop to the containerized MemoPad is using `mcp-proxy`, which converts the HTTP transport to STDIO that Claude Desktop expects:

1. **Start the Docker container:**
   ```bash
   docker-compose up -d
   ```

2. **Configure Claude Desktop** to use mcp-proxy:
   ```json
   {
     "mcpServers": {
       "memopad": {
         "command": "uvx",
         "args": [
           "mcp-proxy",
           "http://localhost:8000/mcp"
         ]
       }
     }
   }
   ```


## Support

For Docker-specific issues:

1. Check the [troubleshooting section](#troubleshooting) above
2. Review container logs: `docker-compose logs memopad`
3. Verify volume mounts: `docker inspect memopad-server`
4. Test file permissions: `docker exec memopad-server ls -la /app`

For general MemoPad support, see the main [README](../README.md)
and [documentation](https://memory.basicmachines.co/).

## GitHub Container Registry Images

### Available Images

Pre-built Docker images are available on GitHub Container Registry at [`ghcr.io/basicmachines-co/memopad`](https://github.com/basicmachines-co/memopad/pkgs/container/memopad).

**Supported architectures:**
- `linux/amd64` (Intel/AMD x64)
- `linux/arm64` (ARM64, including Apple Silicon)

**Available tags:**
- `latest` - Latest stable release
- `v0.13.8`, `v0.13.7`, etc. - Specific version tags
- `v0.13`, `v0.12`, etc. - Major.minor tags

### Automated Builds

Docker images are automatically built and published when new releases are tagged:

1. **Release Process:** When a git tag matching `v*` (e.g., `v0.13.8`) is pushed, the CI workflow automatically:
   - Builds multi-platform Docker images
   - Pushes to GitHub Container Registry with appropriate tags
   - Uses native GitHub integration for seamless publishing

2. **CI/CD Pipeline:** The Docker workflow includes:
   - Multi-platform builds (AMD64 and ARM64)
   - Layer caching for faster builds
   - Automatic tagging with semantic versioning
   - Security scanning and optimization

### Setup Requirements (For Maintainers)

GitHub Container Registry integration is automatic for this repository:

1. **No external setup required** - GHCR is natively integrated with GitHub
2. **Automatic permissions** - Uses `GITHUB_TOKEN` with `packages: write` permission
3. **Public by default** - Images are automatically public for public repositories

The Docker CI workflow (`.github/workflows/docker.yml`) handles everything automatically when version tags are pushed.