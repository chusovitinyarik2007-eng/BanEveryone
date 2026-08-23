class user:
    def __init__(self, uuid='n/a', name='n/a', max_device='n/a', current_device=[]):
        self.uuid = uuid
        self.name = name
        self.max_device = max_device
        self.current_device = current_device

    @property
    def __str__(self):
        return (f'UUID: {self.uuid}\n'
                f'Name: {self.name}\n'
                f'Limit: {self.max_device}\n'
                f'Current devices: {len(self.current_device)}\n')


def convert_to_user(uuid, limit, hwids, name):
    return user(uuid, name, limit, hwids)