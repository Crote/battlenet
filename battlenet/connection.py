import logging
import urllib2
import base64
import hmac
import hashlib
import time
import urlparse
import gzip
import StringIO
from .things import Character, Realm, Guild, Reward, Perk, Class, Race
from .exceptions import APIError, API304, CharacterNotFound, GuildNotFound, RealmNotFound
from .utils import quote

# Fixing HTTPS
import httplib
import socket
import ssl

def Safeconnect(self):
    "Connect to a host on a given (SSL) port."
    # As stated in the urllib2 documentation, it doesn't provide ANY kind of certificate validation, hence making https worthless.
    # By hardcoding the certs in this function, we can force it to validate our certs regardless.
    # I have absolutely no idea what the effect could be on anything importing this api library, but this solves our issues for now.

    from battlenet import CERT
    sock = socket.create_connection((self.host, self.port), self.timeout, self.source_address)
    if self._tunnel_host:
        self.sock = sock
        self._tunnel()
    self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file, cert_reqs=ssl.CERT_REQUIRED, ca_certs=CERT)

httplib.HTTPSConnection.connect = Safeconnect
# HTTPS should be secure now.

try:
    import simplejson as json
except ImportError:
    import json

try:
    from eventlet.green import urllib2 as eventlet_urllib2
except ImportError:
    eventlet_urllib2 = None

__all__ = ['Connection']

URL_FORMAT = 'https://%(host)s/api/%(game)s%(path)s'

logger = logging.getLogger('battlenet')

DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun',)
MONTHS = ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul',
          'Aug', 'Sep', 'Oct', 'Nov', 'Dec',)


class Connection(object):
    defaults = {
        'eventlet': False,
        'public_key': None,
        'private_key': None
    }

    def __init__(self, public_key=None, private_key=None,
                 game='wow', eventlet=None):

        self.public_key = public_key or Connection.defaults.get('public_key')
        self.private_key = private_key or Connection.defaults.get('private_key')
        self.game = game
        self.eventlet = eventlet or Connection.defaults.get('eventlet', False)

        self._cache = {}

    def __eq__(self, other):
        if not isinstance(other, Connection):
            return False

        return self.game == other.game

    def __ne__(self, other):
        return not self.__eq__(other)

    @staticmethod
    def setup(**defaults):
        Connection.defaults.update(defaults)

    def sign_request(self, method, now, url, private_key):
        string_to_sign = '%s\n%s\n%s\n' % (method, now, url)
        hash = hmac.new(private_key, string_to_sign, hashlib.sha1).digest()
        return base64.encodestring(hash).rstrip()

    def make_request(self, region, path, params=None, cache=False, lastModified=None):
        params = params or {}

        now = time.gmtime()
        date = '%s, %2d %s %d %2d:%02d:%02d GMT' % (DAYS[now[6]], now[2],
            MONTHS[now[1]], now[0], now[3], now[4], now[5])

        headers = {
            'Date': date,
            'Accept-Encoding': 'gzip'
        }

        url = URL_FORMAT % {
            'host': region + '.battle.net' if region != 'cn' else 'battlenet.com.cn',
            'game': self.game,
            'path': path
        }
        if len(params):
            url_params = '?%(params)s' % {
                'params': '&'.join('='.join(
                (k, ','.join(v) if isinstance(v, (set, list)) else v))
                for k, v in params.items() if v)
            }
            url = url + url_params

        if cache and url in self._cache:
            return self._cache[url]

        uri = urlparse.urlparse(url)

        if self.public_key:
            signature = self.sign_request('GET', date, uri.path, self.private_key)
            headers['Authorization'] = 'BNET %s:%s' % (self.public_key, signature)
        if lastModified:
            headers['If-Modified-Since'] = '%s, %s %s %s %s:%s:%s GMT' % (DAYS[lastModified.weekday()], lastModified.strftime('%d'), 	# This looks really ugly,
            MONTHS[int(lastModified.strftime('%m'))], lastModified.strftime('%Y'), lastModified.strftime('%H'), 						# but it seems to do the job.
            lastModified.strftime('%M'), lastModified.strftime('%S'))

        logger.debug('Battle.net => ' + url)

        request = urllib2.Request(url, None, headers)

        if self.eventlet and eventlet_urllib2:
            try:
                response = eventlet_urllib2.urlopen(request)
            except (eventlet_urllib2.URLError, eventlet_urllib2.HTTPError), e:
                if isinstance(e, eventlet_urllib2.HTTPError) and e.code == 304:
                    raise API304()
                else:
                    raise APIError(str(e))
        else:
            try:
                response = urllib2.urlopen(request)
            except (urllib2.URLError, urllib2.HTTPError), e:
                if isinstance(e, urllib2.HTTPError) and e.code == 304:
                    raise API304()
                else:
                    print e
                    raise APIError(str(e))

        if 'content-encoding' in response.info() and response.info()['content-encoding'] == 'gzip':
            response = gzip.GzipFile(fileobj=StringIO.StringIO(response.read()))
                # StringIO is needed here due to Gzip seeking back/forwards in the file-like object, but the remote server provides a stream of bytes, 
                # which isn't remotely seekable at all, so if you cut out StringIO you basically end up with an empty data set.
                # It's an inelegant hack, but probably the best solution.

        try:
            data = json.loads(response.read())
        except json.JSONDecodeError:
            raise APIError('Non-JSON Response')
        else:
            if data.get('status') == 'nok':
                raise APIError(data['reason'])

        if cache:
            self._cache[url] = data

        return data

    def get_character(self, region, realm, name, fields=None, raw=False, lastModified=None):
        name = quote(name.lower())
        realm = quote(realm.lower())
        try:
            data = self.make_request(region, '/character/%s/%s' % (realm, name), {'fields': fields} if fields else None, lastModified=lastModified)

            if raw:
                return data

            return Character(region, data=data, connection=self)
        except APIError:
            raise CharacterNotFound

    def get_guild(self, region, realm, name, fields=None, raw=False, lastModified=None):
        name = quote(name.lower())
        realm = quote(realm.lower())

        try:
            data = self.make_request(region, '/guild/%s/%s' % (realm, name), {'fields': fields} if fields else None, lastModified=lastModified)

            if raw:
                return data

            return Guild(region, data=data, connection=self)
        except APIError:
            raise GuildNotFound

    def get_all_realms(self, region, raw=False):
        data = self.make_request(region, '/realm/status')

        if raw:
            return data['realms']

        return [Realm(region, data=realm, connection=self) for realm in data['realms']]

    def get_realms(self, region, names, raw=False):
        data = self.make_request(region, '/realm/status', {'realms': ','.join(map(quote, names))})

        if raw:
            return data['realms']

        return [Realm(region, data=realm, connection=self) for realm in data['realms']]

    def get_realm(self, region, name, raw=False):
        data = self.make_request(region, '/realm/status', {'realm': quote(name.lower())})

        if len(data['realms']) != 1:
            raise RealmNotFound

        if raw:
            return data['realms'][0]

        return Realm(self, region, data=data['realms'][0], connection=self)

    def get_guild_perks(self, region, raw=False):
        data = self.make_request(region, '/data/guild/perks', cache=True)
        perks = data['perks']

        if raw:
            return perks

        return [Perk(region, perk) for perk in perks]

    def get_guild_rewards(self, region, raw=False):
        data = self.make_request(region, '/data/guild/rewards', cache=True)
        rewards = data['rewards']

        if raw:
            return rewards

        return [Reward(region, reward) for reward in rewards]

    def get_character_classes(self, region, raw=False):
        data = self.make_request(region, '/data/character/classes', cache=True)
        classes = data['classes']

        if raw:
            return classes

        return [Class(class_) for class_ in classes]

    def get_character_races(self, region, raw=False):
        data = self.make_request(region, '/data/character/races', cache=True)
        races = data['races']

        if raw:
            return races

        return [Race(race) for race in races]

    def get_item(self, region, item_id, raw=False):
        data = self.make_request(region, '/item/%d' % item_id)
        return data
