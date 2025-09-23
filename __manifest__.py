{
    'name': 'Cash Transfer Simple',
    'version': '17.0.1.0.1',
    'summary': 'Transferencia simple entre diarios (sin chequeo de saldo)',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'views/cash_transfer_views.xml'
    ],
    'installable': True,
    'application': False
}