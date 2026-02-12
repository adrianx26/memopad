import os

# set config.env to "test" for pytest to prevent logging to file in utils.setup_logging()
os.environ["MEMOPAD_ENV"] = "test"
