import unittest
import pipeline as p

class Delivery(unittest.TestCase):
    def test_guidance_is_removed_without_losing_narration_timing(self):
        spoken='근데 이게 좀 이상하잖아요.'
        value='[Conversational] '+spoken
        alignment={'characters':list(value),'character_start_times_seconds':[i*.05 for i in range(len(value))],'character_end_times_seconds':[(i+1)*.05 for i in range(len(value))]}
        result=p.narration_alignment(spoken,{'alignment':alignment})
        self.assertEqual(''.join(result['characters']),spoken)
        self.assertEqual(len(result['character_end_times_seconds']),len(spoken))
        self.assertGreater(result['character_start_times_seconds'][0],0)

    def test_mismatched_audio_never_receives_invented_caption_times(self):
        with self.assertRaisesRegex(ValueError,'requires review'):
            p.narration_alignment('Actual script',{'alignment':{'characters':list('Unrelated words')}})
