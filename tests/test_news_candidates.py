import datetime as dt
import io
import unittest
import urllib.error
from email.utils import format_datetime
from unittest.mock import patch

import pipeline as p

NOW=dt.datetime(2026,11,5,12,tzinfo=dt.timezone.utc)

def rss(items):
    return ('<rss><channel>'+''.join('<item><title>'+title+'</title><link>'+url+'</link><pubDate>'+date+'</pubDate></item>' for title,url,date in items)+'</channel></rss>').encode()

def atom(items):
    return ('<feed xmlns="http://www.w3.org/2005/Atom">'+''.join('<entry><title>'+title+'</title><link href="'+url+'"/><'+field+'>'+date+'</'+field+'></entry>' for title,url,date,field in items)+'</feed>').encode()

EMPTY=b'<rss><channel/></rss>'

class NewsCandidates(unittest.TestCase):
    def read(self,payloads):
        def fetch(request,**kwargs):
            value=payloads.get(request.full_url,EMPTY)
            if isinstance(value,Exception):raise value
            return io.BytesIO(value)
        with patch.object(p,'now',return_value=NOW),patch.object(p.urllib.request,'urlopen',side_effect=fetch):return p.news_candidates()

    def test_rss_filters_stale_future_naive_and_insecure_leads(self):
        entries=[]
        for label,seconds in [('fresh',60),('boundary',72*3600),('stale',72*3600+1),('clock_skew',-300),('future',-301)]:
            entries.append((label,'https://example.org/'+label,format_datetime(NOW-dt.timedelta(seconds=seconds))))
        entries.extend([('no_timezone','https://example.org/no-zone','Thu, 05 Nov 2026 11:00:00'),('insecure','http://example.org/http',format_datetime(NOW))])
        leads,errors=self.read({'https://www.nasa.gov/feed/':rss(entries)})
        self.assertEqual([lead['title'] for lead in leads],['fresh','boundary','clock_skew'])
        self.assertEqual(errors,[])
        self.assertTrue(all(lead['publisher']=='NASA' and lead['role_hint']=='primary' and lead['verified'] is False for lead in leads))

    def test_namespaced_atom_supports_published_and_updated_dates(self):
        leads,errors=self.read({'https://www.theverge.com/rss/index.xml':atom([('Published story','https://www.theverge.com/published','2026-11-05T11:00:00Z','published'),('Updated story','https://www.theverge.com/updated','2026-11-05T09:00:00+00:00','updated')])})
        self.assertEqual([lead['url'] for lead in leads],['https://www.theverge.com/published','https://www.theverge.com/updated'])
        self.assertTrue(all(lead['publisher']=='The Verge' and lead['role_hint']=='original_reporting' and not lead['verified'] for lead in leads))
        self.assertEqual(errors,[])

    def test_one_failed_feed_preserves_other_publishers_fresh_leads(self):
        leads,errors=self.read({'https://www.nasa.gov/feed/':rss([('NASA story','https://www.nasa.gov/news/one',format_datetime(NOW))]),'https://feeds.bbci.co.uk/news/rss.xml':urllib.error.URLError('simulated unavailable feed'),'https://www.theverge.com/rss/index.xml':atom([('Verge story','https://www.theverge.com/two',NOW.isoformat(),'published')])})
        self.assertEqual([lead['publisher'] for lead in leads],['NASA','The Verge'])
        self.assertEqual(errors,[{'publisher':'BBC','error':'URLError'}])

    def test_feed_article_budget_is_eight_per_publisher(self):
        payload=rss([(str(i),'https://example.org/story-'+str(i),format_datetime(NOW)) for i in range(12)])
        leads,errors=self.read({url:payload for url in ('https://www.nasa.gov/feed/','https://feeds.bbci.co.uk/news/rss.xml','https://www.theverge.com/rss/index.xml')})
        self.assertEqual(len(leads),24)
        self.assertEqual(errors,[])
        self.assertEqual({publisher:sum(row['publisher']==publisher for row in leads) for publisher in ('NASA','BBC','The Verge')},{'NASA':8,'BBC':8,'The Verge':8})

    def test_news_leads_keep_collection_usable_when_both_trends_regions_fail(self):
        lead={'title':'Fresh story','url':'https://example.org/article','published_at':NOW.isoformat(),'publisher':'BBC','role_hint':'original_reporting','verified':False}
        with patch.object(p,'now',return_value=NOW),patch.object(p.urllib.request,'urlopen',side_effect=urllib.error.URLError('simulated trend failure')),patch.object(p,'news_candidates',return_value=([lead],[])),patch.object(p,'write') as save:
            result=p.collect()
        self.assertEqual(result['topic_count'],0)
        self.assertEqual(len(result['errors']),2)
        packet=save.call_args_list[0].args[1]
        self.assertEqual(packet['news_article_candidates'],[lead])
        self.assertIn('not verified evidence',packet['notice'])


if __name__=='__main__':unittest.main()

