from collections import defaultdict
from users.models import TrackTag, ArtistTag,Track

def build_tag_vector_for_track(track_id:int)->dict[str, str]:

    track = Track.objects.get(id=track_id)

    vector={}
    tags=TrackTag.objects.filter(track=track,is_active=True).select_related('tag')

    for tt in tags:
        vector[tt.tag_id]=max(vector.get(tt.tag_id,0.0),tt.weight)

    return vector