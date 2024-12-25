import random
import string

from locust import HttpUser, TaskSet, task, between

users = [f'test_user{n}' for n in range(1, 10000)]
user_iterator = iter(users)


def get_next_user():
    return next(user_iterator)


def generate_random_string(length=10):
    # Указываем символы, которые можно использовать
    characters = string.ascii_letters + string.digits + string.punctuation
    # Генерируем строку заданной длины
    random_string = ''.join(random.choices(characters, k=length))
    return random_string


class UserBehavior(TaskSet):

    def on_start(self):
        """Логин или регистрация пользователя при старте."""
        # self.register_user()
        self.user_name = get_next_user()
        self.token = None

        self.login_user()
        self.root_blocks_ids = None

    def register_user(self):
        response = self.client.post("api/v1/register/", json={
            "username": self.user_name,
            "password": "test_password"
        })
        if response.status_code == 201:
            self.token = response.json()['access']

    def login_user(self):
        """Логин пользователя, если регистрация уже выполнена."""

        if not self.token:
            response = self.client.post("api/v1/login/", json={
                "username": self.user_name,
                "password": "test_password"
            })
            if response.status_code == 200:
                self.token = response.json()['access']
            else:
                response = self.client.post("api/v1/register/", json={
                    "username": self.user_name,
                    "password": "test_password"
                })
                if response.status_code == 201:
                    self.token = response.json()['access']


    @task(1)
    def get_root_block(self):
        if self.token:
            headers = {"Authorization": f"Bearer {self.token}"}
            res = self.client.get("api/v1/root-block/", headers=headers)
            if res.status_code == 200:
                data = res.json()
                data.pop('root')
                self.root_blocks_ids = list(data.keys())

    @task(10)
    def copy_block(self):
        if self.token and self.root_blocks_ids:
            headers = {"Authorization": f"Bearer {self.token}"}
            self.client.post(
                "api/v1/copy-block/",
                json={
                    'dest': random.choice(self.root_blocks_ids),
                    'src': [random.choice(self.root_blocks_ids), random.choice(self.root_blocks_ids)]
                },
                headers=headers
            )

    @task(20)
    def create_block(self):
        """Тест на создание нового блока."""
        if self.token and self.root_blocks_ids:
            headers = {"Authorization": f"Bearer {self.token}"}

            self.client.post(
                f"api/v1/new-block/{random.choice(self.root_blocks_ids)}/",
                json={
                    "title": "New Test Block",
                },
                headers=headers,
                name="/api/v1/new-block/")

    @task(30)
    def edit_block(self):
        if self.token and self.root_blocks_ids:
            headers = {"Authorization": f"Bearer {self.token}"}
            block_id = random.choice(self.root_blocks_ids)
            self.client.post(
                f"api/v1/edit-block/{random.choice(self.root_blocks_ids)}/",
                json={
                    'title': generate_random_string(random.randint(1, 20)),
                    'data': {
                        'text': generate_random_string(random.randint(1, 300))
                    }
                },
                headers=headers,
                name="/api/v1/edit-block/")


class WebsiteUser(HttpUser):
    tasks = [UserBehavior]
    wait_time = between(1, 5)
