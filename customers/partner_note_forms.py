from django import forms
from .partner_notes import MAX_NOTE_LENGTH, clean_note_body


class PartnerNoteForm(forms.Form):
    body = forms.CharField(
        label='یادداشت همکار', required=False, max_length=MAX_NOTE_LENGTH,
        widget=forms.Textarea(attrs={'rows': 5, 'maxlength': MAX_NOTE_LENGTH}),
    )
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def clean_body(self):
        return clean_note_body(self.cleaned_data['body'])
