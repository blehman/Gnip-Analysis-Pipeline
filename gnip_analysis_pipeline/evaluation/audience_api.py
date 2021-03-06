import requests
import yaml
import json
import logging
import os
import re
from requests_oauthlib import OAuth1
from math import ceil
from farmhash import hash64 as farmhash64

logger = logging.getLogger('audience_api')

class AudienceApiException(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)
class CredentialsException(AudienceApiException):
    pass
class NoSegmentsFoundException(AudienceApiException):
    pass
class AudienceTooSmallException(AudienceApiException):
    pass
class SegmentDeleteException(AudienceApiException):
    pass
class SegmentQueryException(AudienceApiException): 
    pass
class SegmentCreateException(AudienceApiException): 
    pass
class SegmentPostIdsException(AudienceApiException):
    pass
class AudiencePostException(AudienceApiException):
    pass
class AudienceInfoException(AudienceApiException):
    pass

def add_users(user_ids, unique_id, max_upload_size = 100000, max_segment_size = 3000000):
    """
    Split the users into segments based on maximum segment size
    """

    segment_name_base = unique_id

    unique_user_ids = list(set(user_ids)) 
    num_user_ids = len(unique_user_ids)
    num_segments = int(ceil(len(unique_user_ids)/float(max_segment_size)))
    size_segments = int(ceil(len(unique_user_ids)/float(num_segments)))
   
    logger.debug('{num_user_ids} user ids requires {num_segments} segments of size {size_segments}'.format(**locals()))
    for i, user_id_chunk in enumerate(chunks(unique_user_ids, size_segments)):
        logger.debug('Processing segment: ' + segment_name_base + '_' + str(i) )
        add_segment(user_id_chunk, segment_name_base + '_' + str(i), max_upload_size) 


def add_segment(user_ids, segment_name, max_upload_size = 100000):
    """
    Create segment:
    If segment already exists, get it.
    Otherwise, make it
    """

    # Set up credentials 
    creds,auth = get_authentication()
    
    base_url = creds['url']
    json_header = {'Content-Type' : 'application/json'}
    
    # split ids into max upload size 
    user_id_chunks = chunks(list(set(user_ids)), max_upload_size)
    uids_json_encoded = []
    for uid_chunk in user_id_chunks:
        uids_json_encoded.append(json.dumps({'user_ids': [str(x) for x in uid_chunk]}))

    # check for existence of segment
    segment_check_response = requests.get(base_url + '/segments'
            , auth = auth
            , headers = json_header
    )
    logger.debug('segment_check_response code/text: ' + str(segment_check_response.status_code) + ':\n' + segment_check_response.text)
    
    segment_id = None
    # get segment if existing
    if 'segments' not in segment_check_response.json():
        raise SegmentQueryException(segment_check_response.text) 
    for existing_segment in segment_check_response.json()['segments']:
        if segment_name == existing_segment['name']:
            segment_id = existing_segment['id']   
            num_users = existing_segment['num_user_ids']
            logger.debug('Found segment id {}; name {}, with {} user ids'.format(segment_id,segment_name,num_users))
    # if not existing, create the new segment
    if segment_id is None:
        logger.info('Segment not created; adding it')
        segment_creation_response = requests.post(base_url + '/segments'
                , auth = auth
                , headers = json_header
                , data = json.dumps({'name': segment_name})
                )
        if segment_creation_response.status_code > 299:
            raise SegmentCreateException(segment_creation_response.text) 
        logger.debug('segment_creation_resonse text:\n' + segment_creation_response.text)
        segment_id = segment_creation_response.json()['id']
        
        # upload the chunks of user ids to a segment
        for num,uid_chunk_json_encoded in enumerate(uids_json_encoded):
            segment_post_ids = requests.post(base_url + '/segments/' + segment_id + '/ids'
                    , auth = auth
                    , headers = json_header
                    , data = uid_chunk_json_encoded
                    )
            logger.debug('Uploaded chunk ' + str(num))
            if segment_post_ids.status_code > 299:
                raise SegmentPostIdsException(segment_post_ids.text)
        logger.debug('segment_post_response text:\n' + segment_post_ids.text)

def query_audience(unique_id, groupings):
    """
    Get segments associated with unique_id
    Create and query audience
    """
    creds,auth = get_authentication()

    base_url = creds['url']
    json_header = {'Content-Type' : 'application/json'}
    
    audience_name = unique_id

    # check for existence of segment
    segment_check_response = requests.get(base_url + '/segments'
            , auth = auth
            , headers = json_header
            )
    segment_ids = []
    for entry in segment_check_response.json()['segments']:
        if re.search(r'{}_\d*$'.format(unique_id),entry['name']) is not None:
            logger.debug('adding segment ' + entry['name'] + '/' + entry['id'] + ' to list of matching segments')
            segment_ids.append(entry['id'])
    if len(segment_ids) == 0:
        raise NoSegmentsFoundException('no segments found with base name {}'.format(audience_name))

    # look for existing audience
    audience_query_response = requests.get(base_url + '/audiences'
            , auth = auth
            , headers = json_header
            )
    logger.debug('audience_query_response text:\n' + audience_query_response.text)

    audience_id = None
    # get audience if existing
    for existing_audience in audience_query_response.json()['audiences']:
        if audience_name == existing_audience['name']:
            audience_id = existing_audience['id']
            logger.debug('Found audience name: ' + audience_name + '/ id: ' + audience_id + ' in existing audiences with segment ids ' + str(existing_audience['segment_ids']))

    # make the audience if not existing
    if audience_id is None:
        audience_post = requests.post(base_url + '/audiences'
                , auth = auth
                , headers = json_header
                , data = json.dumps({'name': audience_name, 'segment_ids': segment_ids})
                )
        if audience_post.status_code > 299:
            raise AudiencePostException(audience_post.text) 
        audience_id = audience_post.json()['id']

    # make a request for information about the audience
    audience_info = requests.post(base_url + '/audiences/' + audience_id + '/query'
            , auth = auth
            , headers = json_header
            , data = groupings
            )
    if audience_info.status_code > 299:
        raise AudienceInfoException(audience_info.text) 
    logger.debug(audience_info.json())
    return audience_info.json()

def get_unique_id(user_ids):
    """
    hash the concatenated user IDs and use as segment/audience names
    """
    hashable_str = ''.join([str(i) for i in sorted(user_ids) ] )
    return hex(farmhash64(hashable_str))[2:] 

def query_users(user_ids,groupings):
    try:
        unique_id = get_unique_id(user_ids)
        add_users(user_ids, str(unique_id))
        results = query_audience(str(unique_id),groupings) 
        return results 
    except AudienceApiException, e:
        return {'error' : str(e)}

def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in xrange(0, len(l), n):
        yield l[i:i+n]

def get_authentication():
    """ 
    Set up credentials and authentication 
    """
    creds_file_path = os.getenv('HOME') + '/.audience_api_creds'
    if not os.path.exists(creds_file_path): 
        raise CredentialsException('Credentials file at $HOME/.audience_api_creds must exists!') 
    creds = yaml.load(open(creds_file_path,'r'))
    try:
        auth = OAuth1(creds['consumer_key'],creds['consumer_secret'],creds['token'],creds['token_secret'])  
    except (TypeError,KeyError), e:
        raise CredentialsException('Credentials file at $HOME/.audience_api_creds must contain the keys: username, consumer_key, consumer_secret, token, token_secret, url') 

    return creds,auth
    
