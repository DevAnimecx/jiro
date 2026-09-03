#!/usr/bin/env node

/**
 * Jiro CLI - Node.js wrapper for Jiro Search API
 * 
 * This CLI wraps the Python-based Jiro server and provides
 * a familiar npm experience for installation and usage.
 */

const { program } = require('commander');
const { execSync, spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const chalk = require('chalk');

// Check if Python is available
function checkPython() {
  try {
    execSync('python --version', { stdio: 'ignore' });
    return true;
  } catch {
    try {
      execSync('python3 --version', { stdio: 'ignore' });
      return true;
    } catch {
      return false;
    }
  }
}

// Check if jirosearch is installed
function checkJiroInstalled() {
  try {
    execSync('python -c "import jiro; print(jiro.__version__)"', { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

// Install jirosearch if not installed
function installJiro() {
  console.log(chalk.yellow('Jiro is not installed. Installing...'));
  try {
    execSync('pip install jirosearch==0.2.1', { stdio: 'inherit' });
    console.log(chalk.green('✓ Jiro installed successfully'));
    return true;
  } catch (error) {
    console.error(chalk.red('Failed to install Jiro:'), error.message);
    return false;
  }
}

// Get Python command
function getPythonCmd() {
  try {
    execSync('python --version', { stdio: 'ignore' });
    return 'python';
  } catch {
    return 'python3';
  }
}

// Main CLI
program
  .name('jiro')
  .description('Jiro Search - Local-first, AI-native web search & scraping')
  .version('0.2.1');

program
  .command('serve')
  .description('Start the Jiro API server')
  .option('-h, --host <host>', 'Host to bind to', '127.0.0.1')
  .option('-p, --port <port>', 'Port to listen on', '8000')
  .option('--no-auth', 'Disable authentication (insecure)')
  .action((options) => {
    if (!checkPython()) {
      console.error(chalk.red('Error: Python 3.11+ is required'));
      process.exit(1);
    }

    if (!checkJiroInstalled()) {
      if (!installJiro()) {
        process.exit(1);
      }
    }

    console.log(chalk.cyan('Starting Jiro Search API...'));
    console.log(chalk.dim(`Host: ${options.host}`));
    console.log(chalk.dim(`Port: ${options.port}`));
    console.log('');
    console.log(chalk.green('API: http://localhost:' + options.port));
    console.log(chalk.green('Docs: http://localhost:' + options.port + '/docs'));
    console.log('');

    const python = getPythonCmd();
    const args = [
      '-m', 'uvicorn',
      'jiro.server:create_app',
      '--host', options.host,
      '--port', options.port,
    ];

    const child = spawn(python, args, { stdio: 'inherit' });
    child.on('error', (error) => {
      console.error(chalk.red('Failed to start server:'), error.message);
      process.exit(1);
    });
  });

program
  .command('dashboard')
  .description('Start the web dashboard')
  .option('-p, --port <port>', 'Port to listen on', '3000')
  .action((options) => {
    if (!checkPython()) {
      console.error(chalk.red('Error: Python 3.11+ is required'));
      process.exit(1);
    }

    if (!checkJiroInstalled()) {
      if (!installJiro()) {
        process.exit(1);
      }
    }

    console.log(chalk.cyan('Starting Jiro Dashboard...'));
    console.log(chalk.green('Dashboard: http://localhost:' + options.port));
    console.log('');

    const python = getPythonCmd();
    const args = ['-m', 'jiro.dashboard'];

    const child = spawn(python, args, { stdio: 'inherit' });
    child.on('error', (error) => {
      console.error(chalk.red('Failed to start dashboard:'), error.message);
      process.exit(1);
    });
  });

program
  .command('mcp')
  .description('Start MCP server for AI agents')
  .action(() => {
    if (!checkPython()) {
      console.error(chalk.red('Error: Python 3.11+ is required'));
      process.exit(1);
    }

    if (!checkJiroInstalled()) {
      if (!installJiro()) {
        process.exit(1);
      }
    }

    const python = getPythonCmd();
    const args = ['-m', 'jiro.mcp'];

    const child = spawn(python, args, { stdio: 'inherit' });
    child.on('error', (error) => {
      console.error(chalk.red('Failed to start MCP server:'), error.message);
      process.exit(1);
    });
  });

program
  .command('search <query>')
  .description('Search the web')
  .option('-e, --engine <engine>', 'Search engine', 'google')
  .option('-t, --type <type>', 'Search type', 'web')
  .option('-n, --num <num>', 'Number of results', '10')
  .action((query, options) => {
    if (!checkPython()) {
      console.error(chalk.red('Error: Python 3.11+ is required'));
      process.exit(1);
    }

    if (!checkJiroInstalled()) {
      if (!installJiro()) {
        process.exit(1);
      }
    }

    const python = getPythonCmd();
    const script = `
import asyncio
import json
from jiro.config import Settings
from jiro.scraping.client import ScrapingClient
from jiro.scraping.engines import SearchOrchestrator
from jiro.models import SearchRequest

async def search():
    settings = Settings.load()
    client = ScrapingClient(settings)
    orchestrator = SearchOrchestrator(settings, client)
    
    req = SearchRequest(
        q="${query}",
        engine="${options.engine}",
        type="${options.type}",
        num=${options.num}
    )
    
    result = await orchestrator.search(req)
    print(json.dumps(result.model_dump(), indent=2))
    
    await client.close()

asyncio.run(search())
`;

    execSync(`${python} -c "${script}"`, { stdio: 'inherit' });
  });

program
  .command('scrape <url>')
  .description('Scrape a URL')
  .option('-f, --format <format>', 'Output format (markdown/text/html/json)', 'markdown')
  .action((url, options) => {
    if (!checkPython()) {
      console.error(chalk.red('Error: Python 3.11+ is required'));
      process.exit(1);
    }

    if (!checkJiroInstalled()) {
      if (!installJiro()) {
        process.exit(1);
      }
    }

    const python = getPythonCmd();
    const script = `
import asyncio
import json
from jiro.extract import scrape_url
from jiro.scraping.client import ScrapingClient
from jiro.config import Settings

async def scrape():
    settings = Settings.load()
    client = ScrapingClient(settings)
    
    result = await scrape_url("${url}", client, fmt="${options.format}")
    print(json.dumps(result, indent=2))
    
    await client.close()

asyncio.run(scrape())
`;

    execSync(`${python} -c "${script}"`, { stdio: 'inherit' });
  });

program
  .command('status')
  .description('Show server status')
  .option('--url <url>', 'Server URL', 'http://localhost:8000')
  .action((options) => {
    const http = require('http');
    const url = `${options.url}/v1/monitor/health`;
    
    console.log(chalk.cyan('Checking Jiro status...'));
    
    http.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const status = JSON.parse(data);
          console.log(chalk.green('✓ Jiro is running'));
          console.log(`  Version: ${status.version}`);
          console.log(`  Status: ${status.status}`);
        } catch {
          console.log(chalk.yellow('Server responded but status unclear'));
        }
      });
    }).on('error', (error) => {
      console.log(chalk.red('✗ Jiro is not running'));
      console.log(chalk.dim(`  Start with: jiro serve`));
    });
  });

program
  .command('config')
  .description('Show current configuration')
  .action(() => {
    const os = require('os');
    const configPath = path.join(os.homedir(), '.jiro', 'config.yaml');
    
    console.log(chalk.cyan('Jiro Configuration'));
    console.log('');
    
    if (fs.existsSync(configPath)) {
      console.log(chalk.green(`Config file: ${configPath}`));
      console.log('');
      console.log(fs.readFileSync(configPath, 'utf8'));
    } else {
      console.log(chalk.yellow('No config file found'));
      console.log(chalk.dim('Create one at: ~/.jiro/config.yaml'));
    }
  });

// Parse arguments
program.parse(process.argv);

// Show help if no command
if (!process.argv.slice(2).length) {
  program.outputHelp();
}