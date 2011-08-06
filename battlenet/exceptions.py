class APIError(Exception):
    pass

class API304(Exception):
    pass

class CharacterNotFound(APIError):
    pass

class GuildNotFound(APIError):
    pass

class RealmNotFound(APIError):
    pass
