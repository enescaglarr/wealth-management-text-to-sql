from Helper.Database import get_connection
from Helper.SqlGuard import make_run_sql
from Helper.VannaObject import MyVanna
from Helper.Credentials import Credentials
import os
from vanna.flask import VannaFlaskApp

os.environ["TOKENIZERS_PARALLELISM"] = "false"


vn = MyVanna()

# This gives the package a function that it can use to run the SQL
conn = get_connection()
vn.run_sql = make_run_sql(conn)  # read-only guard: SELECT only, single statement, TOP injected
vn.run_sql_is_set = True
# print(vn.get_training_data())

app = VannaFlaskApp(
    vn,
    title="Wealth Management Text-to-SQL",
    subtitle="Ask questions about advisors, clients, portfolios, assets and transactions - answered with generated SQL.",
    logo="https://img.icons8.com/fluency/96/combo-chart.png",
    allow_llm_to_see_data=True,   # lets the LLM peek at column values for questions like "which risk profiles exist?"
    debug=False,
)
app.run(port=8084)  # the project's own app owns 8085