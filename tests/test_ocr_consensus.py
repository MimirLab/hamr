from PIL import Image

from audiobook.ocr_consensus import localize_verification, verify_words


def words(labels, scale=1):
    return [[0]*6+[str(x*scale),str(10*scale),str(10*scale),str(8*scale),'88',label]
            for x,label in zip(range(10,100,22),labels)]


def view(labels, scale):
    def load():
        image = Image.new('L',(100*scale,100*scale))
        image.info['words'] = words(labels,scale)
        return image
    return load


def read(image):
    return image.info['words']


def test_ocr_corroboration_corrects_only_an_aligned_word():
    primary = words(('keeper','mace','quiet','gate'))
    readings = [('low',view(('kesper','made','quist','6'),1)),
                ('high',view(('keeper','made','quiet','8'),2))]
    selected, report, uncertain = verify_words(primary,(100,100),readings,read)
    assert [w[11] for w in selected]==['keeper','made','quiet','gate']
    assert [change['word'] for change in report['corrections']]==[1]
    assert uncertain==(3,)

    spans = [{'locator':{'rect':[0,70,65,95]},'issue':None},
             {'locator':{'rect':[65,70,100,95]},'issue':None}]
    localize_verification(spans,selected,uncertain,(0,0,100,100),1)
    assert spans[0]['issue'] is None
    assert 'printed words' in spans[1]['issue']


def test_an_agreeing_view_avoids_an_extra_render():
    def unexpected():
        raise AssertionError('A confirmed reading does not need the last view')

    primary = words(('same','words'))
    chosen,report,uncertain = verify_words(
        primary,(100,100),[('low',view(('same','words'),2)),('high',unexpected)],read)
    assert chosen is primary and report['outcome']=='agreed' and uncertain==()


def test_different_word_counts_never_authorize_an_insertion_or_deletion():
    primary = words(('one','two'))
    versions = [('low',view(('one','three','two'),1)),('high',view(('one','three','two'),2))]
    chosen,report,uncertain = verify_words(primary,(100,100),versions,read)
    assert chosen is primary and report['outcome']=='unresolved_order' and uncertain is None
