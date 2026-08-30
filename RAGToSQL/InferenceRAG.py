from Helper.VannaObject import MyVanna
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

vn = MyVanna()

# print(vn.get_training_data())

print(vn.generate_sql("Tell me the top client with highest portfolio."))

