from dotenv import load_dotenv
load_dotenv()   # must run before any import that reads env vars (e.g. OPENAI_API_KEY)

import asyncio
from services.ari import run

if __name__ == "__main__":
    asyncio.run(run())
