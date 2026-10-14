import os
import tempfile

# Keep test tool calls out of the real logs/mcp.log (set before census_mcp is imported).
os.environ.setdefault("CENSUS_MCP_LOG", os.path.join(tempfile.gettempdir(), "census_mcp_test.log"))
